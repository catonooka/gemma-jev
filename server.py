"""
gemma-jev-server: Jev-compatible local decision API on top of llama-server.

GemmaJev approach (per the open benchmarks): read next-token option logits
from a stock small open model — one forward pass per decision, no prose
generation, no JSON parsing. Endpoint shape follows the Jev/djev contract:

    POST /v1/request  { "state": str, "questions": { name: qspec } }

where qspec is one of:
    { "type": "noul",   "instructions": str }
    { "type": "choice", "instructions": str, "criteria": { label: desc } }
    { "type": "score",  "instructions": str, "criteria": { level: desc } }

Response:
    { "answers": { name: { "value": ..., "probability": p, "distribution": {...} } } }

Backed by llama-server's /completion endpoint: prompt is built so that each
option maps to a distinct single-token marker ("A", "B", ...), we request
max_tokens=1 with top logprobs, and normalize the marker probabilities.
"""

import asyncio
import json
import math
import os
import time
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

UPSTREAM = os.environ.get("GEMMAJEV_UPSTREAM", "http://127.0.0.1:8301")
PORT = int(os.environ.get("GEMMAJEV_PORT", "8300"))
MAX_STATE_CHARS = 20_000
MAX_QUESTIONS = 32
LOGPROB_TOP = 40  # llama-server returns top-N; letters sit far above this when primed

app = FastAPI(title="gemma-jev-local")
client = httpx.AsyncClient(timeout=30.0)


class NoulSpec(BaseModel):
    type: str = "noul"
    instructions: str


class ChoiceSpec(BaseModel):
    type: str = "choice"
    instructions: str
    criteria: dict[str, str] = Field(min_length=1)


class ScoreSpec(ChoiceSpec):
    type: str = "score"


class RequestBody(BaseModel):
    state: str
    questions: dict[str, Any]


LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def build_question_block(name: str, spec: dict[str, Any]) -> tuple[str, list[str]]:
    """Return (prompt block, marker tokens) for one question."""
    kind = spec.get("type", "noul")
    instr = spec.get("instructions", "")
    lines = [f"Question ({kind}): {instr}"]
    if kind == "noul":
        lines.append("Options: A = yes, B = no")
        return "\n".join(lines), ["A", "B"]
    criteria = spec.get("criteria") or {}
    if not criteria:
        raise HTTPException(400, f"question {name!r}: choice/score needs criteria")
    markers = []
    for i, (label, desc) in enumerate(criteria.items()):
        if i >= 20:
            break
        m = LETTERS[i]
        markers.append(m)
        lines.append(f"  {m}. {desc or label}")
    return "\n".join(lines), markers


PROMPT_TMPL = (
    "You are a fast decision module. Read the state, answer each question by "
    "picking one option letter. Answer with the letter only.\n\n"
    "## State\n{state}\n\n## Questions\n{questions}\n\n## Answers\n"
)


async def read_markers(prompt: str, markers: list[str]) -> dict[str, float]:
    """One llama-server chat completion; returns marker -> probability."""
    body: dict = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1,
        "temperature": 0.0,
        "logprobs": True,
        "top_logprobs": LOGPROB_TOP,
        "stream": False,
    }
    if os.environ.get("GEMMAJEV_DISABLE_THINKING", "0") == "1":
        # Thinking-first models (Gemma 4 26B/31B QAT) emit <|channel>thought
        # as the first token, hiding the answer letters. Turn thinking off so
        # the answer position starts at the letter.
        body["chat_template_kwargs"] = {"enable_thinking": False}
    r = await client.post(f"{UPSTREAM}/v1/chat/completions", json=body)
    r.raise_for_status()
    data = r.json()
    lp = (data["choices"][0].get("logprobs") or {}).get("content") or []
    top: dict[str, float] = {}
    if lp:
        for cand in lp[0].get("top_logprobs", []) or []:
            tok = cand["token"].strip()
            top[tok] = max(top.get(tok, -1e9), cand["logprob"])
    # normalize over markers that appeared; absent marker -> tiny floor
    picked = {m: math.exp(top.get(m, -30.0)) for m in markers}
    total = sum(picked.values())
    return {m: v / total for m, v in picked.items()}


@app.get("/health")
async def health():
    return {"ok": True, "upstream": UPSTREAM}


@app.get("/ready")
async def ready():
    try:
        p = await read_markers("Is 2+2 equal to 4? Answer A for yes, B for no.\nAnswer: ", ["A", "B"])
        return {"ok": True, "smoke": p}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"upstream not ready: {e}") from e


@app.post("/v1/request")
async def request(body: RequestBody):
    t0 = time.perf_counter()
    if len(body.state) > MAX_STATE_CHARS:
        raise HTTPException(400, f"state exceeds {MAX_STATE_CHARS} chars")
    if not body.questions or len(body.questions) > MAX_QUESTIONS:
        raise HTTPException(400, f"1..{MAX_QUESTIONS} questions required")
    answers: dict[str, Any] = {}
    # joint read: all questions in ONE prompt, one marker line per answer
    blocks: list[str] = []
    marker_map: dict[str, list[str]] = {}
    label_map: dict[str, list[str]] = {}
    for name, spec in body.questions.items():
        block, markers = build_question_block(name, spec)
        blocks.append(block)
        marker_map[name] = markers
        criteria = spec.get("criteria") or {}
        label_map[name] = list(criteria.keys()) if criteria else ["yes", "no"]
    prompt = PROMPT_TMPL.format(
        state=body.state[:MAX_STATE_CHARS],
        questions="\n\n".join(blocks) + "\n",
    )
    # Single forward pass: we ask for the FIRST question's marker position.
    # For multi-question requests we do per-question reads (still one pass each).
    for name, spec in body.questions.items():
        q_prompt = PROMPT_TMPL.format(
            state=body.state[:MAX_STATE_CHARS],
            questions=build_question_block(name, spec)[0] + "\n",
        )
        dist = await read_markers(q_prompt, marker_map[name])
        labels = label_map[name]
        best = max(dist, key=dist.get)  # type: ignore[arg-type]
        idx = marker_map[name].index(best)
        answers[name] = {
            "value": labels[idx],
            "probability": round(dist[best], 4),
            "distribution": {labels[i]: round(dist[marker_map[name][i]], 4) for i in range(len(labels))},
        }
    dt = (time.perf_counter() - t0) * 1000
    return {"answers": answers, "usage": {"decision_ms": round(dt, 1)}}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.environ.get("GEMMAJEV_HOST", "127.0.0.1"), port=PORT)

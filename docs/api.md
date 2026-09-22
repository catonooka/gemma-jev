# gemma-jev API

`server.py` exposes a Jev-shaped local decision API. One decision = one forward pass = one logprob read.

## `POST /v1/request`

```json
{
  "state": "string, up to 20,000 chars — the evidence the model judges",
  "questions": {
    "<name>": { "type": "noul",   "instructions": "yes/no question" },
    "<name>": { "type": "choice", "instructions": "question",
                "criteria": { "label": "description", ... } },
    "<name>": { "type": "score",  "instructions": "rubric question",
                "criteria": { "level": "description", ... } }
  }
}
```

- Max 32 questions per request (each is evaluated as its own single-pass read).
- `choice`/`score`: up to 20 criteria; order matters only for display.

### Response

```json
{
  "answers": {
    "urgent": {
      "value": "yes",
      "probability": 0.9999,
      "distribution": { "yes": 0.9999, "no": 0.0001 }
    },
    "team": {
      "value": "engineering",
      "probability": 0.9981,
      "distribution": { "billing": 0.0007, "engineering": 0.9981, "other": 0.0012 }
    }
  },
  "usage": { "decision_ms": 33.5 }
}
```

`probability` = normalized share of the chosen label among the option letters' logprobs. `distribution` = the full normalized distribution over labels.

## How the read works

Each option maps to a single letter token (`A`, `B`, `C`…). The prompt renders the state, the question, and lettered options, ending at the answer position. llama-server's `/v1/chat/completions` is called with `max_tokens: 1, temperature: 0, logprobs: true, top_logprobs: 40`; the letters' logprobs are softmax-normalized over the option set. Absent letters get a −30 floor. No text is generated; no JSON is parsed out of prose.

Swap the backend freely: any OpenAI-compatible server that returns `top_logprobs` works (`GEMMAJEV_UPSTREAM`). Bigger backend model → better fine-grained accuracy, same API.

## Other endpoints

- `GET /health` — process liveness + upstream URL.
- `GET /ready` — runs one real decision read ("Is 2+2 equal to 4?"); 503 if upstream is down.

## Design rules for good decisions (learned the hard way)

1. **State carries evidence, not labels.** "smoke" as state gives garbage; actual page text or job descriptions give 95%+ accuracy.
2. **Prefer few, orthogonal options.** Binary (noul) and 3-way reads are near-perfect; 10 semantically-close classes degrade (SNIPS 66.7%).
3. **Compose signals in code.** One choice read is a weak arbiter; several binary reads + code combination beat any single clever prompt.
4. **Calibration is directional, not perfect.** High probability ≠ correctness on ambiguous inputs; use thresholds (≥0.6–0.8) for gates, not raw argmax, when the cost of a wrong keep is high.

# sato-jev

**A local, open, super-fast Jev alternative for Sato** — Google Gemma 4 E4B reading single-token option logits on llama.cpp, driving a real visible Chrome browser as a web controller.

Jev (TypeSafe AI's "System One" decision model) costs ~$0.00004/call at 352ms over a network. This stack runs the same *shape* of decision — typed state in, label + probability + full distribution out — at **~33ms p50 on a single RTX 3090, for free, fully offline**.

```
┌─────────────┐    /v1/request     ┌──────────────────┐    logprob read    ┌─────────────────┐
│  Sato chat  │ ─────────────────▶ │  simplejev       │ ─────────────────▶ │ llama-server    │
│  web_agent  │   noul/choice/     │  server (:8300)  │   one forward pass │ Gemma 4 E4B Q8  │
│  tool       │ ◀───────────────── │                  │ ◀───────────────── │ (GPU0)          │
└─────────────┘   typed answers    └──────────────────┘   label probs        └─────────────────┘
                                             │
                                             ▼ decisions gate every step
                                    ┌──────────────────────┐
                                    │ webagent-cdp.py      │
                                    │ real Chrome via CDP  │  ← you watch it browse
                                    │ (:9222, headed)      │
                                    └──────────────────────┘
```

## What's in here

| File | What it is |
|---|---|
| `server.py` | **simplejev** — Jev-shaped decision API (`POST /v1/request`: `noul` / `choice` / `score` questions → answers with probabilities + distributions). One logprob read per decision. |
| `webagent-cdp.py` | Jev-controlled web agent driving your **visible Chrome** over CDP: search, scroll, harvest, open detail pages. Built against Indeed's bot walls. |
| `webagent.py` | Headless variant (Playwright chromium) for bulk collection on friendly sites. |
| `jobcrawl.py` | Public job-API pipeline: Arbeitnow + Remotive → local jev gates every listing → LAN Qwen structures winners to JSON. |
| `bench_jev.py` | Latency + accuracy benchmark (60 timed decisions; 22-case labeled decision set). |
| `jevbench_mini.py` | **JevBench-method evaluation** using the same public datasets the independent Jev harness used: SNIPS, Banking77, InjecAgent-style prompt injection. |
| `test_interact.py` | Browser-interaction gauntlet: site filters, detail-page extraction, pagination — pass/fail logged. |
| `sato-integration/` | The Sato (dsh) side: `web_agent` tool package + registration patch. |
| `docs/` | Setup, API reference, benchmark results, Indeed playbook. |

## Quick start

```bash
# 1. model (one-time): Gemma 4 E4B Q8_0 GGUF from unsloth
huggingface-cli download unsloth/gemma-4-E4B-it-GGUF gemma-4-E4B-it-Q8_0.gguf --local-dir ~/models/gemma4-e4b

# 2. backend: llama-server on GPU0
CUDA_VISIBLE_DEVICES=0 llama-server \
  -m ~/models/gemma4-e4b/gemma-4-E4B-it-Q8_0.gguf \
  -ngl 99 -c 16384 --host 127.0.0.1 --port 8301 --alias gemma4-e4b

# 3. decision API
python server.py          # -> http://127.0.0.1:8300

# 4. your first decision
curl http://127.0.0.1:8300/v1/request -H 'Content-Type: application/json' -d '{
  "state": "The checkout is down. Customers cannot pay.",
  "questions": {
    "urgent": {"type": "noul", "instructions": "Does this need immediate attention?"},
    "team": {"type": "choice", "instructions": "Which team should handle this?",
             "criteria": {"billing": "Charges, invoices, refunds",
                          "engineering": "Broken features and outages",
                          "other": "Anything else"}}
  }}'
```

To drive your visible Chrome (watch it browse):

```bash
# launch a headed Chromium with CDP (profile persists)
DISPLAY=:0 XAUTHORITY=/run/user/1000/.mutter-Xwaylandauth.* \
  chrome --no-sandbox --user-data-dir=~/.chrome-jev-profile \
         --remote-debugging-port=9222

python webagent-cdp.py --query ".NET developer" \
  --goal ".NET developer job listings" --want 20
```

## Measured performance

### Latency (60 decisions, warm)

| | sato-jev (local) | djev (hosted) | Jev 1.13 (hosted) |
|---|---|---|---|
| Model | Gemma 4 E4B Q8 (8.2GB) | DiffusionGemma 26B | closed |
| Hardware | 1× RTX 3090 | their cluster | their cluster |
| p50 | **33.5 ms** | ~240 ms | 352 ms |
| p95 | 80.9 ms | — | 818 ms |
| cost | electricity (~165W) | $0.035/M input | ~$0.00004/call |

### Accuracy (JevBench-method, same datasets as the independent Jev harness)

| Task | sato-jev | Jev published | n |
|---|---|---|---|
| Banking77 intent (8-label subset) | **100%** | 80.3% (full 77) | 40 |
| InjecAgent-style prompt injection | 91.7% | ~100% P/R @thr 0.10 | 12 |
| SNIPS intent (10 classes) | 66.7% | 97.9% (7 classes) | 30 |
| Crawler decisions (own labeled set) | 95.5% | — | 22 |

Honest reading: genuinely competitive on banking-intent and injection-safety tasks; Jev clearly keeps the crown on fine-grained multi-class intent (SNIPS). For controller work — binary/3-way gates like scroll/extract/relevance — it's more than enough, at ~10x the speed and zero cost. See `docs/benchmarks.md`.

## Why "SimpleJev" style

The reference open implementation (Davipar/djev-dev) pins DiffusionGemma-26B in BF16 (~52GB, TP=1, one GPU per process) — not feasible on consumer dual-24GB rigs. The SimpleJev approach instead reads **next-token option logits from a stock small open model**: build a prompt where each option maps to a single letter token (A/B/C…), run one forward pass, normalize the letters' logprobs into a distribution. No prose generation, no JSON parsing, no special architecture. Any OpenAI-compatible server with a logprobs endpoint works.

## Lessons baked into this code (the valuable part)

1. **Never URL-jump into search results on bot-walled sites.** Indeed's edge block triggers on deep links but lets the same query typed into the site's own search box through. Navigate via site controls.
2. **Compose signal questions in code, never trust one choice read.** The action-choice question waffles (p≈0.5) at page bottom or when tired; orthogonal binary signals (`found?`, `more_below?`) are the calibrated ones. Ask both, combine in code. Same finding as the published Jev harness research.
3. **Chat endpoint for logprobs, not raw completion.** Gemma's IT chat template changes the first token distribution; raw `/completion` reads a newline. Use `/v1/chat/completions` with `logprobs`.
4. **Anonymous Indeed caps:** ~16 cards/page, login wall on paging past page 2, ~10 rapid searches → soft rate limit. Workarounds: variant queries + merge + title/company dedupe.
5. **SPA card clicks get swallowed.** Extract the `jk` id from card links and navigate to `/viewjob?jk=…` directly.

Full details in `docs/indeed-playbook.md`.

## Sato integration

`sato-integration/` contains the `@deepseek-ai/dsh-tool-web-agent` package (a `web_agent` tool for the Sato chat app) plus the exact registration patch. From Sato you ask *"get me 20 .NET jobs"* → the model calls `web_agent` with `query=".NET developer"` → your visible Chrome opens Indeed, the local jev decides every scroll/extract step, and structured jobs land in the chat. See `sato-integration/README.md`.

## License

MIT. Model weights: Gemma 4 is Apache 2.0 (Google). This project is not affiliated with TypeSafe AI, Maisa, or Google; "Jev" comparisons are for evaluation purposes.

## Status

Working stack, benchmarks, and integration verified end-to-end (2026-09-22). Numbers in `docs/` are from this rig: Threadripper PRO 3955WX, 2× RTX 3090, Ubuntu 24+, llama.cpp CUDA.

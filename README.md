# gemma-jev

**A local, open, fast Jev alternative** — Google Gemma 4 E4B reading single-token option logits on llama.cpp. Typed decisions with probabilities, ~30ms on a GPU, ~0.1-0.3s on modern CPUs, $0, fully offline, one 8GB model file.

Jev (TypeSafe AI's closed "System One" decision model) costs ~$0.00004/call at 352ms over a network. gemma-jev runs the same *shape* of decision — state in, label + probability + full distribution out — using the SimpleJev technique: no special architecture, no prose generation, no JSON parsing. Just one forward pass and a logprob read.

```
┌────────────┐   /v1/request    ┌──────────────────┐   logprob read    ┌─────────────────┐
│ your code  │ ───────────────▶ │  gemma-jev API   │ ───────────────▶ │ llama-server    │
│ (CLI/agent)│ ◀─────────────── │    (:8300)       │ ◀─────────────── │ Gemma 4 E4B     │
└────────────┘  typed answers   └──────────────────┘  label probs      │ GPU / CPU / Mac │
                                                                        └─────────────────┘
```

## Performance — the full hardware picture

Measured decision latency (warm, ~100-token state, one logprob read):

| Machine | Mode | Model file | Latency | Notes |
|---|---|---|---|---|
| **RTX 3090** | GPU (llama.cpp CUDA) | E4B Q8_0 (8.2GB) | **~33ms** p50, 53ms p95 | measured; 5.9GB VRAM |
| RTX 3090 | GPU | E4B **Q4_K_M (5.0GB, default)** | ~20-25ms (est.) | halved reads |
| **Threadripper PRO 3955WX** | CPU, 8 threads (Docker) | E4B **Q4_K_M** | **~221ms** p50, 232ms p95 | measured |
| Threadripper PRO 3955WX | CPU, 8 threads | E4B Q8_0 | ~320ms | measured |
| **Apple M4 Pro** | Metal via llama.cpp | E4B Q4_K_M | **~50-80ms (est.)** | 273GB/s unified memory — near-GPU |
| Apple M4 Pro | Metal | E4B Q8_0 | ~90-150ms (est.) | |
| Intel i7-12600H | CPU (laptop) | E4B Q4_K_M | ~350-600ms (est.) | dual-channel DDR5 is the ceiling |

Q4_K_M is the default model file: 40% smaller download, ~30% faster CPU decisions, and identical accuracy on every decision benchmark we run (controller set 95.5% and Banking77 100% on both quants; SNIPS 66.7% Q8 vs 70.0% Q4 — within noise). Q8_0 remains a drop-in if you want maximum headroom.

For scale: hosted Jev 1.13 answers in ~352ms median (818ms p95) at $0.00004/call + network; djev ~240ms. A 3090 beats both by ~10x at electricity cost; an M4 Pro roughly ties or beats them with zero marginal cost and full privacy.

The backend is swappable — any OpenAI-compatible server with logprobs works. A Gemma 4 26B-A4B QAT q4_0 on one 3090 measures 48ms p50 and lifts accuracy (SNIPS 66.7%→86.7%, injection 91.7%→100%) while also serving as a full LLM (141-145 t/s) from the same process; set `GEMMAJEV_DISABLE_THINKING=1` because the QAT build emits a thinking-channel token first. Full comparison in `docs/benchmarks.md`.

## Accuracy

JevBench-method evaluation (same dataset families as the independent Jev harness):

| Task | gemma-jev (E4B) | Jev (published) | n |
|---|---|---|---|
| Banking77 intent (8-label subset) | **100%** | 80.3% (full 77) | 40 |
| InjecAgent-style prompt injection | **91.7%** | ~100% P/R | 12 |
| SNIPS intent (10 classes) | 66.7% | 97.9% (7 classes) | 30 |
| Controller decisions (own labeled set) | **95.5%** | — | 22 |

Honest reading: excellent at binary/few-option judgments (the controller/guardrail sweet spot); a 4.5B model loses resolution on 10-way fine-grained intent. Either swap the backend model or use two-stage reads ("compete first, verify second"). Raw results in `results/`.

## What's in the repo

| File | What it is |
|---|---|
| `server.py` | **gemma-jev API** — Jev-shaped decision service (`POST /v1/request`: `noul`/`choice`/`score` → value + probability + distribution). One logprob read per decision. |
| `jev` | **CLI** for agents (Claude Code, Hermes, custom harnesses). Stdlib-only; single-line output; `--json`; `--min-p` confidence gate. See `CLI.md`. |
| `setup.sh` | Host launcher: model download (first run), GPU autodetect with CPU fallback, start/stop/status. |
| `docker/` | Two-file compose: CPU default, GPU override file. Verified both. |
| `webagent-cdp.py` | Jev-controlled browsing in your **visible Chrome** (CDP) — Indeed-proven. |
| `webagent.py` | Headless variant for bulk collection. |
| `jobcrawl.py` | Public job APIs → jev gates each listing → LLM structures winners. |
| `bench_jev.py`, `jevbench_mini.py`, `test_interact.py` | Latency/accuracy benchmarks + browser-interaction gauntlet. |
| `docs/` | Setup, API reference + question-writing rules, benchmarks, Indeed bot-wall playbook. |

## Quick start

### Any machine with Docker (CPU first, GPU opt-in)

```bash
git clone https://github.com/catonooka/gemma-jev && cd gemma-jev/docker
# any of these work:
#   hf download unsloth/gemma-4-E4B-it-GGUF gemma-4-E4B-it-Q4_K_M.gguf --local-dir ./models
#   curl -L -o models/gemma-4-E4B-it-Q4_K_M.gguf "https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/resolve/main/gemma-4-E4B-it-Q4_K_M.gguf"
docker compose up -d                                        # CPU
# GPU host (nvidia toolkit installed):
#   docker compose -f docker-compose.yml -f compose-gpu.yml up -d

jev ask "The checkout is down. Customers cannot pay." "urgent?"
# → yes
```

### Linux/macOS host, no Docker

```bash
./setup.sh start        # downloads model on first run; GPU autodetect, CPU fallback
jev ask "Job: Senior .NET dev, C# ASP.NET" "is this a .NET job?"
# → yes
```

### Apple Silicon (M4 Pro and friends)

```bash
brew install llama.cpp
llama-server -m ~/models/gemma-4-E4B-it-Q4_K_M.gguf -ngl 99 -c 8192 --port 8301
python3 server.py       # from this repo
jev ask "anything" "yes or no question?"
```
The Docker path also works unmodified (arm64 image). `-ngl 99` routes layers to Metal.

### Driving a real browser (the fun part)

```bash
# launch a headed Chrome with CDP (profile persists, beats bot walls)
chrome --user-data-dir=~/.chrome-jev-profile --remote-debugging-port=9222
python webagent-cdp.py --query ".NET developer" --goal "job listings" --want 20
```
Local jev decides every step — scroll/collect/stop, link gating — while you watch it browse. Field notes for bot-walled sites in `docs/indeed-playbook.md`.

## The technique (why this works)

Each option maps to a single letter token (A/B/C…). The prompt renders state + lettered options and stops at the answer position; llama-server is called with `max_tokens: 1` + `logprobs`; the letters' logprobs are softmax-normalized into the answer distribution. No text generation, no JSON to parse, no hallucination surface. Because decisions are prefill-bound (≈100 tokens in, 1 token out), they are cheap on CPUs and Apple Silicon — not just GPUs.

Design rules that make it accurate (learned the hard way, in `docs/api.md`):

1. **State = evidence.** Paste real text, never labels.
2. **Few, orthogonal options.** Binary and 3-way reads hit 95-100%; 10 similar options degrade.
3. **Compose in code.** One choice read waffles at p≈0.5 under ambiguity — ask orthogonal binary questions and combine them. Termination and thresholds are *code's* job.
4. **Pair Choice with Noul** when "none of the above" exists — a Choice always crowns a winner.

## Where it fits

```
LLM (rare):      plan, write, handle surprises
gemma-jev (often): judge, classify, gate, rank — hundreds of calls
code (always):   thresholds, composition, enforcement, math
```

Proven shapes: tool-call gating, RAG passage screening + reranking, guardrails (jailbreak/PII/policy), intent routing, model cascades, bulk labeling, browser-agent control, stuck-loop supervision. It replaces every place you'd ask an LLM a question whose answer is a label.

## License

MIT. Gemma 4 weights are Apache 2.0 (Google), downloaded separately. Not affiliated with TypeSafe AI or Google; "Jev" comparisons are for evaluation.

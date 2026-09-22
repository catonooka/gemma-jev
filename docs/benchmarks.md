# Benchmarks

Measured 2026-09-22 on the reference rig (Threadripper PRO 3955WX, RTX 3090 #0, Gemma 4 E4B Q8_0, llama.cpp CUDA). Raw outputs: `../results/bench_results.json`, `../results/jevbench_mini.json`.

## Latency — `bench_jev.py`

60 mixed noul/choice decisions after warmup, sequential:

| Metric | ms |
|---|---|
| min | 33.1 |
| **p50** | **33.5** |
| p95 | 80.9 |
| p99 / max | 145.4 |
| mean | 38.8 |

Context: Jev 1.13 hosted median 352ms (p95 818ms); djev hosted ~240ms p50. Local is ~10x / ~7x faster respectively, with no network dependency. p95/p99 spikes are llama-server scheduling jitter, not a trend. Throughput scales further by batching (`options.samples`, multiple questions per request are per-question reads today).

## Accuracy — two test sets

### 1. Controller-decision set (`bench_jev.py`, 22 labeled cases)

Covers the decisions the crawler actually makes: urgency triage, team routing, .NET-job relevance (incl. Vietnamese titles), tech-vs-non-tech, seniority, download gating, scroll continuation.

**Result: 21/22 = 95.5%**

Only miss: "misaligned footer logo" flagged urgent (p=0.996) — the model treats any user-reported defect as urgent. Conservative bias; wrong on strict labels.

### 2. JevBench-method set (`jevbench_mini.py`)

Same dataset families the independent Jev harness used, same decision shape, smaller samples:

| Task | sato-jev | Jev (published) | n | Note |
|---|---|---|---|---|
| Banking77 intent (8-label subset) | **100%** | 80.3% | 40 | ours is an easier 8-label subset of their 77 |
| InjecAgent-style prompt injection | **91.7%** | ~100% P/R @thr 0.10 | 12 | caught 5/6 attack phrasings |
| SNIPS intent (10 classes) | 66.7% | 97.9% | 30 | theirs used 7 different classes |

Honest reading: competitive on banking-intent and injection safety; clearly behind on fine-grained multi-class intent. Single-token letter reads lose resolution when options are semantically close ("SearchPlace" vs "GetPlaceDetails"). If a use case needs fine multi-class, either (a) swap the backend model (any OpenAI-compatible server with logprobs — try Gemma 4 26B-A4B Q4), or (b) do a two-stage read: letter-choice shortlist → per-candidate noul verification (the "compete first, verify second" pattern from the Jev harness research).

## End-to-end crawl timings

| Task | Time | Notes |
|---|---|---|
| Search + harvest 16 Indeed cards | 9.4s | 2 jev decisions; page loads dominate |
| Search + site filter + 3 detail pages + classify 16 | ~90s | browser-bound; jev ≈ 0.3s of it |
| 268 API candidates → jev-filtered + structured | ~190s | 762 sequential decisions (~30ms each) |

The decision layer is never the bottleneck; the website is.

## Reproducing

```bash
python bench_jev.py        # latency + controller set
python jevbench_mini.py    # JevBench-method set (needs network for HF datasets)
```

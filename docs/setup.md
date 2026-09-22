# Setup

Tested on: Threadripper PRO 3955WX, 2× RTX 3090 (NVLink), Ubuntu 24+, CUDA llama.cpp build at `/opt/llama.cpp`, Python 3.12 venv.

## 1. Model

Gemma 4 E4B IT, Q8_0 GGUF (8.2 GB) from unsloth:

```bash
huggingface-cli download unsloth/gemma-4-E4B-it-GGUF \
  gemma-4-E4B-it-Q8_0.gguf --local-dir ~/models/gemma4-e4b
```

Other quants work; Q8_0 measured the best accuracy/speed balance here. The model uses ~5.9GB VRAM at 16k context — a 3090 runs it alongside other work.

## 2. Backend — llama-server (port 8301)

```bash
CUDA_VISIBLE_DEVICES=0 llama-server \
  -m ~/models/gemma4-e4b/gemma-4-E4B-it-Q8_0.gguf \
  -ngl 99 -c 16384 --host 127.0.0.1 --port 8301 --alias gemma4-e4b
```

Notes:
- Use the **`/v1/chat/completions`** endpoint with `logprobs` — not raw `/completion`. Gemma's chat template changes the first-token distribution; raw completion reads a newline. This is wired into `server.py` already.
- Under load GPU0 draws ~165W; idle serving is ~33W.

## 3. Decision server — simplejev (port 8300)

```bash
pip install fastapi uvicorn httpx pydantic
python server.py
# env: SIMPLEJEV_UPSTREAM (default http://127.0.0.1:8301), SIMPLEJEV_PORT (8300)
```

Health: `GET /health` · Readiness (runs one real read): `GET /ready`

## 4. Browser for webagent-cdp (port 9222)

A real **headed** Chrome/Chromium with a persistent profile:

```bash
DISPLAY=:0 XAUTHORITY=/run/user/1000/.mutter-Xwaylandauth.<suffix> \
  ~/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome \
  --no-sandbox \
  --user-data-dir=~/.chrome-jev-profile \
  --remote-debugging-port=9222
```

- `--no-sandbox` is required on Ubuntu 24+ (unprivileged userns + AppArmor restriction).
- The persistent profile (`~/.chrome-jev-profile`) carries cookies/history, which materially improves bot-wall pass rates. Logging into one real account in this profile unlocks pagination on Indeed.
- Headless `webagent.py` needs Playwright + its chromium (`pip install playwright`).

## 5. Optional: LAN Qwen for structured output

`jobcrawl.py` uses an OpenAI-compatible endpoint for JSON structuring (any works):

```bash
# env: QWEN base URL + QWEN_MODEL; default http://gx10-qwen.local/v1, qwen3.8-flash-next
# thinking mode off via chat_template_kwargs in the script
```

## Ports summary

| Port | Service |
|---|---|
| 8301 | llama-server (Gemma 4 E4B) |
| 8300 | simplejev decision API |
| 9222 | headed Chrome CDP |
| 3095 | Sato chat app (optional integration) |

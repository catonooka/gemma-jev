# Sato integration — the `web_agent` tool

This directory holds the Sato-side piece: a `web_agent` tool for the Sato chat app (a lean fork of deepseek-harness) that exposes the local jev controllers to the chat model.

## What the user sees

In Sato: *"I need to see 20 jobs on .NET"* → the model calls `web_agent` → your visible Chrome opens the site, the local Gemma jev decides every step (search → scroll → harvest → detail pages), and structured results land in the chat with per-round decision probabilities.

## Two modes

| Args | Mode | Controller |
|---|---|---|
| `query` (+ optional `location`, `want`) | **CDP**: drives your visible Chrome via `:9222` | `webagent-cdp.py` |
| `url` (+ `goal`) | **headless**: fast bulk collection | `webagent.py` |

## Install into a Sato checkout

1. Copy the package:

```bash
cp -r sato-integration/tool-web-agent  <sato>/packages/web/tool-web-agent
```

2. Register it in the chat bundle (`packages/bundle/chat-app/src/index.ts`), next to the browser tool registration:

```ts
import { defineWebAgentTool } from '@deepseek-ai/dsh-tool-web-agent/src/index.ts'

ctx.inject(['tools'], (toolsCtx) => {
  toolsCtx.tools.register(defineWebAgentTool({
    script: process.env.SATO_WEBAGENT_SCRIPT ?? '/home/aisever/gemma-jev/webagent.py',
    cdpScript: process.env.SATO_WEBAGENT_CDP_SCRIPT ?? '/home/aisever/gemma-jev/webagent-cdp.py',
    python: process.env.SATO_WEBAGENT_PYTHON ?? '/home/aisever/vllm-env/bin/python',
    jevUrl: process.env.GEMMAJEV_URL ?? undefined,
    timeoutMs: 300_000,
  }))
})
```

3. Add to `packages/bundle/chat-app/package.json` dependencies:

```json
"@deepseek-ai/dsh-tool-web-agent": "workspace:^",
```

4. `pnpm install && pnpm run build` — the full repo `tsc -b tsconfig.host.json` passes with this package.

## Environment overrides

| Var | Default | Meaning |
|---|---|---|
| `SATO_WEBAGENT_SCRIPT` | `~/gemma-jev/webagent.py` | headless controller |
| `SATO_WEBAGENT_CDP_SCRIPT` | `~/gemma-jev/webagent-cdp.py` | CDP controller |
| `SATO_WEBAGENT_PYTHON` | `~/vllm-env/bin/python` | interpreter (needs playwright + httpx) |
| `GEMMAJEV_URL` | `http://127.0.0.1:8300` | decision server |

## Requirements at runtime

- gemma-jev server up (`../server.py`, port 8300)
- for CDP mode: the headed Chrome on `:9222` (see `../docs/setup.md`)
- for headless mode: Playwright chromium installed

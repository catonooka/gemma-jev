# gemma-jev → LiteLLM: expose the decision API through your local proxy

Two ways to wire it, depending on what you want LiteLLM to do.

## Option A — pass-through (recommended, zero config)

gemma-jev is **not a chat model** — it answers typed decisions, not
conversations. The cleanest LiteLLM integration is to treat it as an
internal tool your proxy *calls*, not a model it *serves*:

```python
# in your LiteLLM router/handler code
from litellm import router  # your existing setup

def decide(state: str, question: str, options: dict | None = None):
    """Call gemma-jev directly (bypasses LiteLLM — it's a tool, not a model)."""
    import requests
    q = {"type": "choice" if options else "noul",
         "instructions": question}
    if options: q["criteria"] = options
    r = requests.post("http://192.168.1.104:8300/v1/request",
                      json={"state": state, "questions": {"q": q}}, timeout=30)
    a = r.json()["answers"]["q"]
    return a["value"], a["probability"]
```

Use it in hooks: `pre_call_hook` (route/guardrail every request),
`post_call_failure_hook` (classify errors), custom routers (model selection
by difficulty Score). This is the pattern the Jec ecosystem uses — decision
model as harness instrumentation, LiteLLM as the model gateway.

## Option B — expose as a fake "model" via custom provider

If you need it callable as `model="gemma-jev/..."` through LiteLLM's
OpenAI-compatible surface (some tools only speak chat completions):

gemma-jev has no /v1/chat/completions endpoint, so bridge it with a tiny
adapter or use LiteLLM's `custom_openai` pointing at a wrapper:

```yaml
# litellm_config.yaml
model_list:
  - model_name: gemma-jev
    litellm_params:
      model: openai/gemma-jev
      api_base: http://192.168.1.104:8301   # llama-server (raw model, NOT jev)
      api_key: none
  # ^ this exposes the RAW Gemma 4 E4B as a chat model (fine for generation),
  #   but decisions must go through :8300 /v1/request — see Option A.

router_settings:
  num_retries: 1
```

Raw-model exposure (generation, 141 t/s class) works through :8301 as a
plain OpenAI-compatible backend. The decision API (:8300) stays a tool.

## Decision questions that work well through LiteLLM hooks

- request routing: `jev ask "$prompt" "difficulty?" 1=simple 2=standard 3=hard`
  → pick cheap/expensive model in `pre_call_hook`
- guardrail: `"jailbreak attempt?"` / `"contains PII?"` before forwarding
- fallback triage: on failure, `"is this transient?"` → retry vs escalate

## Endpoints cheat sheet

| URL | What |
|---|---|
| `http://192.168.1.104:8300/v1/request` | typed decisions (noul/choice/score + probabilities) |
| `http://192.168.1.104:8300/health` | liveness |
| `http://192.168.1.104:8301/v1/chat/completions` | raw Gemma 4 E4B (Q4) chat — LiteLLM-compatible as-is |

CLI from any LAN box: `JEV_URL=http://192.168.1.104:8300 jev ask "text" "q?"`

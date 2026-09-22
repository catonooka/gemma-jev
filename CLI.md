# `jev` CLI — for AI agents (Claude Code, Hermes, custom harnesses)

One decision per invocation. Stdlib-only (no pip installs). Output is a single
line on stdout — perfect for shell substitution — or full JSON with `--json`.

## Install
```bash
ln -sf ~/simplejev/jev ~/.local/bin/jev        # or anywhere on PATH
jev-setup start                                 # bring the stack up (first run downloads the model)
```

## Commands

```bash
# yes/no question -> prints "yes" or "no"
jev noul "STATE" "question?"

# same, auto-detected (no options = noul)
jev ask "STATE" "question?"

# choice -> prints the winning label
jev ask "STATE" "which one?" a="first option" b="second option"
jev choice "STATE" "which team?" billing="charges and refunds" engineering="outages"

# ordered rubric -> prints the level
jev score "STATE" "quality?" 1="broken" 2="usable" 3="excellent"

# full answer with probability + distribution
jev ask "STATE" "urgent?" --json
# {"value": "yes", "probability": 0.9999, "distribution": {...}}

# confidence gate: exit 2 (LOW_CONFIDENCE) below threshold — safe for automation
jev ask "STATE" "is this a scam?" --min-p 0.8 || handle_uncertain
```

## Agent patterns

**Bash tool / shell tool:**
```bash
TEAM=$(jev ask "$TICKET_TEXT" "which team handles this?" \
  a=billing b=engineering c=other --min-p 0.7) || TEAM=human_review
```
Exit codes: `0` confident answer on stdout · `1` server down · `2` below
`--min-p`. Missing server prints a hint to run `jev-setup start`.

**Gating risky actions (Claude Code PreToolUse-style hook):**
```bash
#!/bin/bash
# $1 = proposed command, $2 = task context
D=$(jev ask "Task: $2. Proposed command: $1" \
     "Would this command delete, overwrite, or permanently modify data?" --json \
     | python3 -c 'import json,sys; print(json.load(sys.stdin)["probability"])')
if (( $(echo "$D > 0.6" | bc -l) )); then
  echo "block: destructive action, ask the user first" >&2; exit 1
fi
```

**Batch classify (map-reduce):**
```bash
cat items.txt | while read -r item; do
  printf '%s\t%s\n' "$item" "$(jev ask "$item" "is this spam?")"
done
```

## Question-writing rules (these decide your accuracy)
1. State = evidence (paste the actual text), never labels.
2. Binary (noul) and few-option choices are near-perfect; 10+ similar options degrade.
3. Pair every Choice with a Noul when "none of the above" is possible — a Choice always picks a winner.
4. Compose in shell/code; never branch on one wobbly choice read.

## Config
- `JEV_URL` — server (default `http://127.0.0.1:8300`)
- `jev-setup start|stop|status` — whole-stack control; env `JEV_GPU`, `JEV_PORT_API`, `JEV_MODEL_DIR`

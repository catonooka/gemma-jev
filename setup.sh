#!/usr/bin/env bash
# gemma-jev setup + launcher: brings the local Jev stack up on any machine.
#
#   setup.sh              download model (first run) + start everything
#   setup.sh start        start llama-server + jev API (skips download if present)
#   setup.sh stop         stop both
#   setup.sh status       ports + GPU state
#
# Env overrides:
#   JEV_MODEL_DIR   (default ~/models/gemma4-e4b)
#   JEV_LLAMA_BIN   (default /opt/llama.cpp/build/bin/llama-server, falls back to PATH)
#   JEV_PORT_API    (default 8300)   JEV_PORT_MODEL   (default 8301)
#   JEV_GPU         (default: auto — first CUDA device; set "cpu" for CPU-only)
#   JEV_CTX         (default 16384)

set -euo pipefail

MODEL_DIR="${JEV_MODEL_DIR:-$HOME/models/gemma4-e4b}"
MODEL_FILE="$MODEL_DIR/gemma-4-E4B-it-Q8_0.gguf"
API_PORT="${JEV_PORT_API:-8300}"
MODEL_PORT="${JEV_PORT_MODEL:-8301}"
CTX="${JEV_CTX:-16384}"
RUN_DIR="$HOME/.local/run/gemma-jev"
mkdir -p "$RUN_DIR"

log() { echo "[gemma-jev] $*"; }

find_llama() {
  if [[ -n "${JEV_LLAMA_BIN:-}" ]]; then echo "$JEV_LLAMA_BIN"; return; fi
  for c in /opt/llama.cpp/build/bin/llama-server ./llama-server "$(command -v llama-server || true)"; do
    [[ -x "$c" ]] && { echo "$c"; return; }
  done
  echo ""
}

up()   { curl -sf --max-time 2 "http://127.0.0.1:$1/health" >/dev/null 2>&1; }

download_model() {
  if [[ -f "$MODEL_FILE" ]]; then log "model present: $MODEL_FILE"; return; fi
  log "downloading Gemma 4 E4B Q8_0 (8.2GB) to $MODEL_DIR ..."
  mkdir -p "$MODEL_DIR"
  if command -v hf >/dev/null 2>&1; then
    hf download unsloth/gemma-4-E4B-it-GGUF gemma-4-E4B-it-Q8_0.gguf --local-dir "$MODEL_DIR"
  elif command -v huggingface-cli >/dev/null 2>&1; then
    huggingface-cli download unsloth/gemma-4-E4B-it-GGUF gemma-4-E4B-it-Q8_0.gguf --local-dir "$MODEL_DIR"
  else
    pip install -q huggingface_hub >/dev/null
    python3 -c "
from huggingface_hub import hf_hub_download
print(hf_hub_download('unsloth/gemma-4-E4B-it-GGUF','gemma-4-E4B-it-Q8_0.gguf', local_dir='$MODEL_DIR'))
"
  fi
  log "download complete"
}

start() {
  if up "$API_PORT"; then log "already running (api :$API_PORT)"; return; fi
  download_model
  LLAMA="$(find_llama)"
  [[ -z "$LLAMA" ]] && { echo "llama-server not found — set JEV_LLAMA_BIN"; exit 1; }

  # GPU autodetect: use CUDA if any NVIDIA device exists, else CPU
  GPU_ARGS=(--host 127.0.0.1 --port "$MODEL_PORT" --alias gemma4-e4b -c "$CTX")
  if [[ "${JEV_GPU:-auto}" == "cpu" ]] || ! command -v nvidia-smi >/dev/null 2>&1; then
    log "CPU mode (no -ngl)"
  else
    GPU_ARGS+=(-ngl 99)
    [[ "${JEV_GPU:-auto}" != "auto" && "${JEV_GPU:-auto}" != "cpu" ]] && export CUDA_VISIBLE_DEVICES="$JEV_GPU"
    log "GPU mode: $(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)"
  fi

  log "starting llama-server on :$MODEL_PORT"
  nohup "$LLAMA" -m "$MODEL_FILE" "${GPU_ARGS[@]}" >"$RUN_DIR/llama.log" 2>&1 &
  echo $! >"$RUN_DIR/llama.pid"

  for i in $(seq 1 60); do up "$MODEL_PORT" && break; sleep 5; done
  up "$MODEL_PORT" || { log "llama-server failed to start — tail of log:"; tail -5 "$RUN_DIR/llama.log"; exit 1; }

  log "starting jev API on :$API_PORT"
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  GEMMAJEV_UPSTREAM="http://127.0.0.1:$MODEL_PORT" GEMMAJEV_PORT="$API_PORT" \
    nohup python3 "$SCRIPT_DIR/server.py" >"$RUN_DIR/jev.log" 2>&1 &
  echo $! >"$RUN_DIR/jev.pid"

  for i in $(seq 1 12); do up "$API_PORT" && break; sleep 1; done
  up "$API_PORT" || { log "jev API failed — tail:"; tail -5 "$RUN_DIR/jev.log"; exit 1; }

  log "UP: model :$MODEL_PORT  jev :$API_PORT   (logs in $RUN_DIR)"
  log 'try:  echo "The site is down" | xargs -0 jev noul - "is this urgent?"'
}

stop() {
  for f in "$RUN_DIR/llama.pid" "$RUN_DIR/jev.pid"; do
    [[ -f "$f" ]] && { kill "$(cat "$f")" 2>/dev/null || true; rm -f "$f"; }
  done
  pkill -f "gemma-jev/server.py" 2>/dev/null || true
  pkill -f "gemma-4-E4B-it-Q8_0" 2>/dev/null || true
  log "stopped"
}

status() {
  for p in "$MODEL_PORT" "$API_PORT"; do
    if up "$p"; then echo ":$p UP"; else echo ":$p DOWN"; fi
  done
  command -v nvidia-smi >/dev/null && nvidia-smi --query-gpu=index,memory.used,power.draw --format=csv,noheader
  [[ -f "$RUN_DIR/llama.pid" ]] && echo "llama pid: $(cat "$RUN_DIR/llama.pid")"
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  status) status ;;
  *) echo "usage: $0 [start|stop|status]"; exit 1 ;;
esac

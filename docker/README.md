# Docker

CPU by default; GPU via override file. Works on any x86_64/arm64 host with Docker.

## First run
```bash
cd docker
hf download unsloth/gemma-4-E4B-it-GGUF gemma-4-E4B-it-Q8_0.gguf --local-dir ./models
# (or) huggingface-cli download ... ; or point JEVMODELS=/path/to/models

docker compose up -d                # CPU
docker compose run --rm cli ask "The site is down" "urgent?"    # CLI check
```

## GPU (host needs nvidia-container-toolkit)
```bash
docker compose -f docker-compose.yml -f compose-gpu.yml up -d
```

## Measured (this rig)
| Mode | decision latency |
|---|---|
| CPU (8 threads, TR Pro 3955WX) | ~0.36s warm |
| GPU (1x 3090) | ~30ms |

CPU mode is fully usable for interactive gating; use GPU for high-volume loops.

Env knobs: `JEVMODELS` (model dir), `JEVPORT` (host API port, default 8300),
`JEVCTX` (context, default 8192 CPU / 16384 GPU), `JEVTHREADS` (CPU threads).

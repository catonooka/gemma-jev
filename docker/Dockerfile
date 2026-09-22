FROM python:3.12-slim

# The jev decision API — pure Python (fastapi + uvicorn + httpx), no model weights.
# The model itself runs in the companion llama-server container (or any host).
RUN pip install --no-cache-dir fastapi==0.136.* 'uvicorn[standard]==0.53.*' httpx==0.28.*

WORKDIR /app
COPY server.py .
COPY jev /usr/local/bin/jev
RUN chmod +x /usr/local/bin/jev

ENV SIMPLEJEV_UPSTREAM=http://llama:8301 \
    SIMPLEJEV_PORT=8300 \
    SIMPLEJEV_HOST=0.0.0.0 \
    JEV_URL=http://localhost:8300 \
    PYTHONUNBUFFERED=1
EXPOSE 8300

HEALTHCHECK --interval=10s --timeout=5s --retries=12 --start-period=5s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8300/health',timeout=3)" || exit 1

CMD ["python", "server.py"]

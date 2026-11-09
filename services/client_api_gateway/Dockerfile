FROM python:3.11-slim-bookworm@sha256:94a8fee35252d06ab75856f15f12c7bae67b10e8a6da93c7fded82b2610926cd

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app:/app/services


COPY locks/client.txt ./requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes --only-binary=:all: -r requirements.lock

COPY config/ ./config/
COPY shared/ ./shared/
COPY services/client_api_gateway/ ./services/client_api_gateway/
COPY services/project_integrations/ ./services/project_integrations/

RUN groupadd --gid 10001 appuser && useradd --uid 10001 --gid appuser --no-create-home appuser && \
    mkdir -p /app/logs && chown appuser:appuser /app/logs

USER appuser

EXPOSE 8005

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8005/health/ready', timeout=3).close()"]

CMD ["python", "-m", "uvicorn", "services.client_api_gateway.main:app", "--host", "0.0.0.0", "--port", "8005"]

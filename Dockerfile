FROM python:3.11-slim-bookworm@sha256:94a8fee35252d06ab75856f15f12c7bae67b10e8a6da93c7fded82b2610926cd

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

WORKDIR /app


COPY locks/management.txt ./requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes --only-binary=:all: -r requirements.lock

COPY config/ ./config/
COPY database/ ./database/
COPY shared/ ./shared/
COPY services/management_service/ ./services/management_service/
COPY services/project_integrations/ ./services/project_integrations/
COPY services/semantic_service/schemas/ff_score.py ./services/semantic_service/schemas/
COPY services/semantic_service/proposals/__init__.py ./services/semantic_service/proposals/
COPY services/semantic_service/proposals/model.py ./services/semantic_service/proposals/
COPY services/semantic_service/proposals/facts.py ./services/semantic_service/proposals/
COPY services/semantic_service/proposals/derive.py ./services/semantic_service/proposals/
COPY services/semantic_service/proposals/validators.py ./services/semantic_service/proposals/
COPY services/event_resilience.py ./services/event_resilience.py

RUN groupadd --gid 10001 appuser && useradd --uid 10001 --gid appuser --no-create-home appuser && \
    mkdir -p /app/logs /app/reports && chown appuser:appuser /app/logs /app/reports

USER appuser

EXPOSE 8004

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8004/health/ready', timeout=3).close()"]

CMD ["uvicorn", "services.management_service.main:app", "--host", "0.0.0.0", "--port", "8004"]

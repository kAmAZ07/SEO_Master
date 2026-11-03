FROM python:3.11-slim-bookworm@sha256:94a8fee35252d06ab75856f15f12c7bae67b10e8a6da93c7fded82b2610926cd

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY locks/semantic.txt ./requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes --only-binary=:all: -r requirements.lock

COPY services/semantic_service /app/services/semantic_service
COPY services/event_resilience.py /app/services/event_resilience.py

ENV PYTHONPATH=/app
RUN groupadd --gid 10001 appuser && useradd --uid 10001 --gid appuser --no-create-home appuser
USER appuser
EXPOSE 8002

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8002/health', timeout=3).close()"]

CMD ["uvicorn", "services.semantic_service.main:app", "--host", "0.0.0.0", "--port", "8002"]

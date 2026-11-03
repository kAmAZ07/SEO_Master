FROM python:3.11-slim-bookworm@sha256:94a8fee35252d06ab75856f15f12c7bae67b10e8a6da93c7fded82b2610926cd

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY locks/tilda.txt ./requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes --only-binary=:all: -r requirements.lock

COPY adapters/tilda-adapter /app/adapters/tilda-adapter
RUN groupadd --gid 10001 appuser && useradd --uid 10001 --gid appuser --no-create-home appuser
USER appuser

EXPOSE 8010

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8010/health', timeout=3).close()"]

CMD ["python", "-m", "uvicorn", "main:app", "--app-dir", "/app/adapters/tilda-adapter", "--host", "0.0.0.0", "--port", "8010"]

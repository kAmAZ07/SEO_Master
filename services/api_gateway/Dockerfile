FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY models.py database_config.py redis_config.py secrets_manager.py logging_config.py ./
COPY client_api_gateway/ ./client_api_gateway/
COPY migrations/ ./migrations/
COPY alembic.ini .

RUN mkdir -p /app/logs && chmod 777 /app/logs

EXPOSE 8005

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8005/health || exit 1

CMD ["python", "-m", "uvicorn", "client_api_gateway.main:app", "--host", "0.0.0.0", "--port", "8005"]

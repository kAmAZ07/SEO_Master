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

COPY models.py database_config.py redis_config.py celery_config.py secrets_manager.py logging_config.py ./
COPY audit_service/ ./audit_service/
COPY semantic_service/ ./semantic_service/
COPY reporting_service/ ./reporting_service/
COPY management_service/ ./management_service/
COPY client_api_gateway/ ./client_api_gateway/
COPY migrations/ ./migrations/
COPY alembic.ini .

RUN mkdir -p /app/logs /app/reports && chmod 777 /app/logs /app/reports

CMD ["celery", "-A", "celery_config", "worker", "--loglevel=info"]

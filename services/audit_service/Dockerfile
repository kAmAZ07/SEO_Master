FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY services/audit_service/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

RUN python -m playwright install chromium

COPY config/ ./config/
COPY database/ ./database/
COPY shared/ ./shared/
COPY services/audit_service /app/services/audit_service
COPY services/project_integrations /app/services/project_integrations
COPY services/event_resilience.py ./services/event_resilience.py

ENV PYTHONPATH=/app

CMD ["uvicorn", "services.audit_service.main:app", "--host", "0.0.0.0", "--port", "8001"]

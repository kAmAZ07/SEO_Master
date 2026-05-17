FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY services/reporting_service/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY services/reporting_service /app/services/reporting_service
COPY services/project_integrations /app/services/project_integrations
COPY services/event_resilience.py /app/services/event_resilience.py

ENV PYTHONPATH=/app

CMD ["uvicorn", "services.reporting_service.main:app", "--host", "0.0.0.0", "--port", "8003"]

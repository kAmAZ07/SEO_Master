FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY services/semantic_service/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY services/semantic_service /app/services/semantic_service
COPY services/event_resilience.py /app/services/event_resilience.py

ENV PYTHONPATH=/app

CMD ["uvicorn", "services.semantic_service.main:app", "--host", "0.0.0.0", "--port", "8002"]

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY services/audit_service/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

RUN python -m playwright install --with-deps chromium

COPY services/audit_service /app/services/audit_service

CMD ["uvicorn", "services.audit_service.main:app", "--host", "0.0.0.0", "--port", "8001"]
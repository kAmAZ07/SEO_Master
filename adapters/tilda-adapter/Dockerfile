FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY adapters/tilda-adapter/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY adapters/tilda-adapter /app/adapters/tilda-adapter

EXPOSE 8010

CMD ["python", "-m", "uvicorn", "main:app", "--app-dir", "/app/adapters/tilda-adapter", "--host", "0.0.0.0", "--port", "8010"]

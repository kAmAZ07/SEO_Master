FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN pip install --no-cache-dir fastapi uvicorn

COPY tests/e2e/mocks/mock_wp_api.py /app/mock_wp_api.py

EXPOSE 8086

CMD ["uvicorn", "mock_wp_api:app", "--host", "0.0.0.0", "--port", "8086"]

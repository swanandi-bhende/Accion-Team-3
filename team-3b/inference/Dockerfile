FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt requirements-dev.txt pyproject.toml ./
RUN pip install --no-cache-dir --retries 10 --timeout 120 -r requirements.txt
COPY common ./common
COPY inference ./inference
CMD ["uvicorn", "inference.app:app", "--host", "0.0.0.0", "--port", "8000"]

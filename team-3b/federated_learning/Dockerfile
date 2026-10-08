FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt requirements-dev.txt pyproject.toml ./
RUN pip install --no-cache-dir --retries 10 --timeout 120 -r requirements.txt
COPY common ./common
COPY federated_learning ./federated_learning
CMD ["python", "-m", "federated_learning.runner"]

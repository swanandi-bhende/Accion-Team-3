FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt requirements-dev.txt pyproject.toml ./
RUN pip install --no-cache-dir --retries 10 --timeout 120 -r requirements.txt
COPY common ./common
COPY event_listener ./event_listener
COPY proof_submitter ./proof_submitter
COPY scripts ./scripts
CMD ["python", "-m", "proof_submitter.runner"]

FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt requirements-dev.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt
COPY common ./common
COPY event_listener ./event_listener
CMD ["python", "-m", "event_listener.runner"]
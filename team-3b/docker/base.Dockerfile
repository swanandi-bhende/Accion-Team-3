# syntax=docker/dockerfile:1

# Shared foundation for every Python service in this repository.
#
# One place installs the common runtime dependencies and the tooling the compose
# healthchecks need, so the five service images cannot silently drift apart.
# docker-compose.yml builds this image as the `base` service and each service
# links to it with `additional_contexts`, so `docker compose up --build` builds it
# first with no manual step.
#
# Service-specific extras stay in the service Dockerfiles: flwr only in
# federated_learning/Dockerfile, the EZKL toolchain only in proof_submitter's.
#
# Both stages pin python:3.11-slim, matching the interpreter the test suite runs
# against in CI.

FROM python:3.11-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY requirements/base.txt ./requirements/base.txt
RUN pip install --upgrade pip \
 && pip install --retries 10 --timeout 120 -r requirements/base.txt


FROM python:3.11-slim AS base

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# curl backs the HTTP healthchecks declared in docker-compose.yml.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

# Services run unprivileged; /app/data is the one writable location they need
# (proving keys, local scratch). Images that must install extra packages switch
# back to root for that step and return to this user afterwards.
RUN useradd --create-home --shell /usr/sbin/nologin app \
 && mkdir -p /app/data \
 && chown -R app:app /app

WORKDIR /app
USER app

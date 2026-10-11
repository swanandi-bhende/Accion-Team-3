# syntax=docker/dockerfile:1

# Inference API (FastAPI + TEE runtime). The shared base image already provides
# the common dependencies, so this only adds the service's own modules.
FROM accion-base:latest

COPY common ./common
COPY inference ./inference
# Entrypoint tooling for this service's own data: the Model Registry seeder that
# docker-compose runs once as the seed-model init service.
COPY scripts ./scripts

EXPOSE 8000
CMD ["uvicorn", "inference.app:app", "--host", "0.0.0.0", "--port", "8000"]

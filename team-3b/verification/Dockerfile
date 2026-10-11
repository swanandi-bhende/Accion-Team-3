# syntax=docker/dockerfile:1

# Verification API (FastAPI). Serves POST /verify for TEE attestations and zkML
# proofs, and resolves payloads through the model storage backend, so it ships
# the verifier implementations from proof_generator as well.
FROM accion-base:latest

COPY common ./common
COPY proof_generator ./proof_generator
COPY verification ./verification

EXPOSE 8001
CMD ["uvicorn", "verification.app:create_default_app", "--factory", "--host", "0.0.0.0", "--port", "8001"]

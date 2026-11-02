# syntax=docker/dockerfile:1

# Verification API (FastAPI). Serves POST /verify for TEE attestations and zkML
# proofs, and resolves payloads through the model storage backend, so it ships
# the verifier implementations from proof_generator as well.
FROM base

# Every package, so a service never misses an import another package needs.
COPY src ./

# EZKL proofs are checked against the proving bundles in ZKML_PROVING_KEY_STORE, so the
# ezkl package is needed on any host that verifies them. It has no linux/arm64 wheel.
ARG INSTALL_ZKML=false
COPY requirements/zkml.txt ./requirements/zkml.txt
USER root
RUN if [ "$INSTALL_ZKML" = "true" ]; then \
      pip install --retries 10 --timeout 120 -r requirements/zkml.txt; \
    fi
USER app

EXPOSE 8001
CMD ["uvicorn", "verification.app:create_default_app", "--factory", "--host", "0.0.0.0", "--port", "8001"]

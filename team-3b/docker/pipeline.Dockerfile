# syntax=docker/dockerfile:1

# Composed pipeline: the event listener, inference, proving, proof submission and
# verification wired in one process by inference.pipeline.build_pipeline. Its
# imports span every service package, so this image copies all of them; the
# single-service images each copy only their own.
#
# It replaces the standalone event-listener service in docker-compose.yml. Both
# would follow the chain and write the same listener state in Redis.
FROM base

COPY src/common ./common
COPY src/event_listener ./event_listener
COPY src/inference ./inference
COPY src/proof_generator ./proof_generator
COPY src/proof_submitter ./proof_submitter
COPY src/verification ./verification

# ZKML_BACKEND=ezkl needs the ezkl package, which has no linux/arm64 wheel. Build with
# --build-arg INSTALL_ZKML=true on a linux/amd64 host; the default stays on the development
# backend, which the pipeline cannot put on chain.
ARG INSTALL_ZKML=false
COPY requirements/zkml.txt ./requirements/zkml.txt
USER root
RUN if [ "$INSTALL_ZKML" = "true" ]; then \
      pip install --retries 10 --timeout 120 -r requirements/zkml.txt; \
    fi
USER app

CMD ["python", "-m", "inference.pipeline"]

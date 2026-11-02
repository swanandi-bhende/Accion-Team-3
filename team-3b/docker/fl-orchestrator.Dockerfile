# syntax=docker/dockerfile:1

# Federated Learning Orchestrator. flwr is this service's own dependency, so the
# extra is installed here and nowhere else.
#
# The runner trains the configured job and exits (a one-shot service in
# docker-compose.yml); federated_learning.network_runner is the long-lived
# gRPC/SuperLink entrypoint used for real multi-process runs.
FROM base

# Every package, so a service never misses an import another package needs.
COPY src ./
# Installing into the shared virtualenv needs root; the base image drops to the
# unprivileged user again afterwards.
USER root
COPY requirements/fl.txt ./requirements/fl.txt
RUN pip install --retries 10 --timeout 120 -r requirements/fl.txt
USER app


CMD ["python", "-m", "federated_learning.runner"]

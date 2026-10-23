# syntax=docker/dockerfile:1

# Proof Submitter: a long-lived worker that batches proofs and settles them on
# chain, with retries and a Redis dead-letter queue.
#
# It loads the contract ABI through event_listener.config.load_contract_abi, so
# that package ships here too, and reads the ABI from the read-only mocks mount
# declared in docker-compose.yml.
#
# The EZKL/ONNX toolchain is opt-in: the running proof path is pure Python (see
# requirements/zkml.txt) and ezkl publishes no linux/arm64 wheel, so enabling it
# by default would break the build on Apple Silicon.
FROM base

# Every package, so a service never misses an import another package needs.
COPY src ./

ARG INSTALL_ZKML=false
COPY requirements/zkml.txt ./requirements/zkml.txt
RUN if [ "$INSTALL_ZKML" = "true" ]; then \
      pip install --retries 10 --timeout 120 -r requirements/zkml.txt; \
    fi

CMD ["python", "-m", "proof_submitter.runner"]

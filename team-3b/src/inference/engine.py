import asyncio
import hashlib
import os
import time
import uuid

from redis.asyncio import Redis

from common.exceptions import ModelNotFoundError, TimeoutExceededError
from common.model_registry import ModelRegistry
from common.models import ExecutionReceipt, InferenceRequest
from inference.tee_runtime import TEERuntime


class InferenceEngine:
    """Run model inference in the TEE and return a receipt for each execution.

    Every successful execution is also appended to the ``inference:audit_log`` Redis stream.

    Args:
        registry: Looks up registered models by id and fetches their binaries from storage.
        tee: Runtime that executes the model inside the enclave.
        redis_client: Redis connection used for the audit stream.
        timeout_s: Seconds allowed for one TEE execution before TimeoutExceededError is raised.
        sealed_key: AES-GCM key for encrypted models. A fresh random 256-bit key is generated if omitted.
    """

    def __init__(
        self,
        registry: ModelRegistry,
        tee: TEERuntime,
        redis_client: Redis,
        timeout_s: float = 30.0,
        sealed_key: bytes | None = None,
    ):
        self.registry = registry
        self.tee = tee
        self.redis = redis_client
        self.timeout = timeout_s
        # No key is provisioned in the simulator, so each engine gets a fresh 256-bit key
        # rather than a secret baked into the source. Real TEE key provisioning goes here.
        self.sealed_key = sealed_key if sealed_key is not None else os.urandom(32)

    async def execute(self, request: InferenceRequest) -> ExecutionReceipt:
        """Fetch the requested model, run it in the TEE and return a receipt.

        The receipt holds the execution id, the model CID, SHA-256 hashes of the input
        and output, and the output itself.

        Raises:
            ModelNotFoundError: If the model id is not registered.
            TimeoutExceededError: If the TEE run exceeds the engine's timeout.
        """

        model_entry = await self.registry.get(request.model_id)
        if model_entry is None:
            raise ModelNotFoundError(request.model_id)

        model_binary = await self.registry.storage.retrieve(model_entry.cid)

        start_time = time.time()
        try:
            output_data = await asyncio.wait_for(
                self.tee.execute_inference(
                    model_binary=model_binary,
                    model_hash=model_entry.hash,
                    input_data=request.input_data,
                    sealed_key=self.sealed_key
                ),
                timeout=self.timeout
            )
        except TimeoutError as error:
            raise TimeoutExceededError(self.timeout) from error

        duration = time.time() - start_time

        execution_id = str(uuid.uuid4())
        input_hash = hashlib.sha256(request.input_data).hexdigest()
        output_hash = hashlib.sha256(output_data).hexdigest()

        receipt = ExecutionReceipt(
            execution_id=execution_id,
            model_id=request.model_id,
            model_cid=model_entry.cid,
            input_hash=input_hash,
            output_hash=output_hash,
            output=output_data,
            timestamp=time.time()
        )

        await self._log_execution(execution_id, request.model_id, input_hash, duration, receipt.timestamp)

        return receipt

    async def _log_execution(self, execution_id: str, model_id: str, input_hash: str, duration: float, timestamp: float) -> None:
        """Persist execution to a durable Redis Stream for auditing."""
        audit_entry: dict[str | bytes | memoryview | int | float, str | bytes | memoryview | int | float] = {
            "execution_id": execution_id,
            "model_id": model_id,
            "input_hash": input_hash,
            "duration": str(duration),
            "timestamp": str(timestamp)
        }
        await self.redis.xadd("inference:audit_log", audit_entry)

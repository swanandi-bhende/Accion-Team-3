import time
import asyncio
import uuid
import hashlib
from redis.asyncio import Redis

from common.models import InferenceRequest, ExecutionReceipt
from common.model_registry import ModelRegistry
from inference.tee_runtime import TEERuntime
from common.exceptions import ModelNotFoundError, TimeoutExceededError

class InferenceEngine:
    def __init__(
        self,
        registry: ModelRegistry,
        tee: TEERuntime,
        redis_client: Redis,
        timeout_s: float = 30.0,
        sealed_key: bytes = b"mock_sealed_key_0123456789abcdef"
    ):
        self.registry = registry
        self.tee = tee
        self.redis = redis_client
        self.timeout = timeout_s
        self.sealed_key = sealed_key

    async def execute(self, request: InferenceRequest) -> ExecutionReceipt:
   
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
        except asyncio.TimeoutError:
            raise TimeoutExceededError(self.timeout)
            
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

    async def _log_execution(self, execution_id: str, model_id: str, input_hash: str, duration: float, timestamp: float):
        """Persist execution to a durable Redis Stream for auditing."""
        audit_entry = {
            "execution_id": execution_id,
            "model_id": model_id,
            "input_hash": input_hash,
            "duration": str(duration),
            "timestamp": str(timestamp)
        }
        await self.redis.xadd("inference:audit_log", audit_entry)

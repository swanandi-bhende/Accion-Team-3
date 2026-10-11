"""Run a development proof-generation and verification round trip."""

import asyncio
import hashlib
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import fakeredis.aioredis as fakeredis

from common.model_registry import ModelRegistry
from common.models import ExecutionReceipt
from proof_generator import ZkMLProver
from tests.property.test_model_registry_prop import MockStorageBackend


async def main() -> None:
    redis_client = fakeredis.FakeRedis(decode_responses=False)
    storage = MockStorageBackend()
    registry = ModelRegistry(redis_client, storage)
    model = b"MOCK_LINEAR_WEIGHTS_12345"
    cid = await storage.upload(model)
    await registry.register("toy_linear", cid, model, architecture="linear")

    input_data = b"sanity-input"
    output = b"sanity-output"
    receipt = ExecutionReceipt(
        execution_id="manual-sanity",
        model_id="toy_linear",
        model_cid=cid,
        input_hash=hashlib.sha256(input_data).hexdigest(),
        output_hash=hashlib.sha256(output).hexdigest(),
        output=output,
        timestamp=time.time(),
    )
    prover = ZkMLProver(registry)

    started = time.perf_counter()
    artifact = await prover.generate_proof(receipt)
    elapsed = time.perf_counter() - started

    print(f"backend={prover.BACKEND_NAME}")
    print(f"proof_bytes={len(artifact.proof_bytes)}")
    print(f"generation_seconds={elapsed:.6f}")
    print(f"verified={prover.verify_proof(artifact)}")
    print("WARNING: this development artifact is not a zero-knowledge proof.")


if __name__ == "__main__":
    asyncio.run(main())


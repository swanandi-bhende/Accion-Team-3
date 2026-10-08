import asyncio
from unittest.mock import AsyncMock

import fakeredis.aioredis as fakeredis
import pytest
from pydantic import ValidationError

from common.exceptions import UnsupportedArchitectureError
from common.model_registry import ModelRegistry
from common.models import ExecutionReceipt
from proof_generator import ZkMLProver
from tests.property.test_model_registry_prop import MockStorageBackend


def setup_prover():
    redis_client = fakeredis.FakeRedis(decode_responses=False)
    storage = MockStorageBackend()
    return ModelRegistry(redis_client, storage), storage


@pytest.mark.asyncio
async def test_circuit_cache_is_shared_by_architecture():
    registry, _ = setup_prover()
    prover = ZkMLProver(registry)

    first = await prover._compile_circuit("mlp", "model-a")
    second = await prover._compile_circuit("mlp", "model-b")

    assert first is second


@pytest.mark.asyncio
async def test_proof_generation_timeout_boundary():
    registry, storage = setup_prover()
    model = b"timeout-proof-model"
    cid = await storage.upload(model)
    await registry.register("timeout-proof", cid, model, architecture="linear")
    prover = ZkMLProver(registry, timeout_s=0.01)

    async def slow_compile(*args):
        await asyncio.sleep(1)

    prover._compile_circuit = AsyncMock(side_effect=slow_compile)
    receipt = ExecutionReceipt(
        execution_id="id",
        model_id="timeout-proof",
        model_cid=cid,
        input_hash="input",
        output_hash="output",
        output=b"output",
        timestamp=1.0,
    )

    with pytest.raises(asyncio.TimeoutError):
        await prover.generate_proof(receipt)


def test_malformed_receipt_is_rejected():
    with pytest.raises(ValidationError):
        ExecutionReceipt(
            execution_id="id",
            model_id="model",
            model_cid="cid",
            input_hash="input",
            output_hash="output",
            timestamp=1.0,
        )


@pytest.mark.asyncio
async def test_unknown_model_fails_architecture_detection():
    registry, _ = setup_prover()
    prover = ZkMLProver(registry)

    with pytest.raises(UnsupportedArchitectureError):
        await prover._detect_architecture("missing")

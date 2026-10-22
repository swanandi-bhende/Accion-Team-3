import asyncio
import hashlib

import fakeredis.aioredis as fakeredis
import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from redis.asyncio import Redis

from common.exceptions import ModelNotFoundError
from common.model_registry import ModelRegistry
from common.models import InferenceRequest
from common.tensors import decode_tensor, encode_tensor
from inference.engine import InferenceEngine
from inference.tee_runtime import TEERuntime
from tests.onnx_fixtures import expected_linear_output, linear_model_bytes
from tests.property.test_model_registry_prop import MockStorageBackend

FLOATS = st.floats(min_value=-100, max_value=100, allow_nan=False, width=32)


@pytest.fixture
def redis_client() -> Redis:
    return fakeredis.FakeRedis(decode_responses=False)

@pytest.fixture
def storage() -> MockStorageBackend:
    return MockStorageBackend()

@pytest.fixture
def registry(redis_client: Redis, storage: MockStorageBackend) -> ModelRegistry:
    return ModelRegistry(redis_client, storage)

@pytest.fixture
def tee() -> TEERuntime:
    rt = TEERuntime("sgx")
    rt.initialize()
    return rt

@pytest.fixture
def engine(registry: ModelRegistry, tee: TEERuntime, redis_client: Redis) -> InferenceEngine:
    return InferenceEngine(registry, tee, redis_client, timeout_s=5.0)


@settings(max_examples=10, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(seed=st.integers(min_value=0, max_value=10_000), values=st.lists(FLOATS, min_size=4, max_size=4))
@pytest.mark.asyncio
async def test_property_1_execution_receipt(
    engine: InferenceEngine,
    registry: ModelRegistry,
    storage: MockStorageBackend,
    seed: int,
    values: list[float],
) -> None:
    """Property 1: a valid request yields a receipt with correct ids, hashes and output."""
    model_binary = linear_model_bytes(seed)
    cid = await storage.upload(model_binary)
    model_id = f"test_model_prop1_{seed}"
    await registry.register(model_id, cid, model_binary)
    input_data = encode_tensor(np.array([values], dtype=np.float32))

    receipt = await engine.execute(InferenceRequest(model_id=model_id, input_data=input_data))

    assert receipt.model_id == model_id
    assert receipt.model_cid == cid
    assert receipt.input_hash == hashlib.sha256(input_data).hexdigest()
    assert receipt.output_hash == hashlib.sha256(receipt.output).hexdigest()
    assert np.allclose(
        decode_tensor(receipt.output),
        expected_linear_output(np.array([values], dtype=np.float32), seed),
        rtol=1e-4,
        atol=1e-3,
    )


@settings(max_examples=8, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    values1=st.lists(FLOATS, min_size=4, max_size=4),
    values2=st.lists(FLOATS, min_size=4, max_size=4),
)
@pytest.mark.asyncio
async def test_property_2_isolation_guarantees(
    engine: InferenceEngine,
    registry: ModelRegistry,
    storage: MockStorageBackend,
    values1: list[float],
    values2: list[float],
) -> None:
    """Property 2: concurrent requests do not cross-contaminate their outputs."""
    model_binary = linear_model_bytes(0)
    cid = await storage.upload(model_binary)
    model_id = "test_model_prop2"
    await registry.register(model_id, cid, model_binary)

    x1 = np.array([values1], dtype=np.float32)
    x2 = np.array([values2], dtype=np.float32)
    req1 = InferenceRequest(model_id=model_id, input_data=encode_tensor(x1))
    req2 = InferenceRequest(model_id=model_id, input_data=encode_tensor(x2))

    first_started = asyncio.Event()
    second_started = asyncio.Event()
    release_first = asyncio.Event()
    call_count = 0
    assert engine.tee.enclave is not None
    original_run_isolated = engine.tee.enclave.run_isolated

    async def interleaved_run_isolated(model: bytes, input_data: bytes) -> bytes:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            first_started.set()
            await release_first.wait()
        else:
            await first_started.wait()
            second_started.set()
        return await original_run_isolated(model, input_data)

    engine.tee.enclave.run_isolated = interleaved_run_isolated  # type: ignore[method-assign]

    # Force request B to enter the enclave while request A is paused.
    task1 = asyncio.create_task(engine.execute(req1))
    await first_started.wait()
    task2 = asyncio.create_task(engine.execute(req2))
    await second_started.wait()
    release_first.set()

    receipt1, receipt2 = await asyncio.gather(task1, task2)

    # Each output is the model applied to its own input and to no one else's.
    assert np.allclose(decode_tensor(receipt1.output), expected_linear_output(x1, 0), rtol=1e-4, atol=1e-3)
    assert np.allclose(decode_tensor(receipt2.output), expected_linear_output(x2, 0), rtol=1e-4, atol=1e-3)


@settings(max_examples=10, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(model_id=st.text(min_size=1), input_data=st.binary(min_size=1))
@pytest.mark.asyncio
async def test_property_3_missing_model(engine: InferenceEngine, model_id: str, input_data: bytes) -> None:
    """Property 3: Missing model raises ModelNotFoundError and logs nothing."""
    req = InferenceRequest(model_id=model_id, input_data=input_data)

    with pytest.raises(ModelNotFoundError):
        await engine.execute(req)

    # Verify audit log is empty
    logs = await engine.redis.xread({"inference:audit_log": "0"})
    assert len(logs) == 0

import pytest
import asyncio
import hashlib
from hypothesis import given, settings, strategies as st, HealthCheck
import fakeredis.aioredis as fakeredis

from common.models import InferenceRequest
from common.exceptions import ModelNotFoundError
from inference.engine import InferenceEngine
from inference.tee_runtime import TEERuntime
from common.model_registry import ModelRegistry
from tests.property.test_model_registry_prop import MockStorageBackend

@pytest.fixture
def redis_client():
    return fakeredis.FakeRedis(decode_responses=False)

@pytest.fixture
def storage():
    return MockStorageBackend()

@pytest.fixture
def registry(redis_client, storage):
    return ModelRegistry(redis_client, storage)

@pytest.fixture
def tee():
    rt = TEERuntime("sgx")
    rt.initialize()
    return rt

@pytest.fixture
def engine(registry, tee, redis_client):
    return InferenceEngine(registry, tee, redis_client, timeout_s=5.0)

@settings(max_examples=10, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(model_binary=st.binary(min_size=16, max_size=100), input_data=st.binary(min_size=1, max_size=100))
@pytest.mark.asyncio
async def test_property_1_execution_receipt(engine, registry, storage, model_binary, input_data):
    """Property 1: Valid inputs produce valid receipt with correct hashes."""
    # We must mock encryption for the TEE sim to decrypt properly.
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import os
    sealed_key = engine.sealed_key
    aesgcm = AESGCM(sealed_key)
    nonce = os.urandom(12)
    encrypted_data = nonce + aesgcm.encrypt(nonce, model_binary, None)

    # Upload encrypted model
    cid = await storage.upload(encrypted_data)
    model_id = "test_model_prop1"
    await registry.register(model_id, cid, encrypted_data)

    req = InferenceRequest(model_id=model_id, input_data=input_data)
    receipt = await engine.execute(req)
    
    expected_input_hash = hashlib.sha256(input_data).hexdigest()
    expected_output_hash = hashlib.sha256(receipt.output).hexdigest()

    assert receipt.input_hash == expected_input_hash
    assert receipt.output_hash == expected_output_hash
    assert receipt.model_id == model_id

@settings(max_examples=10, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    input1=st.binary(min_size=1, max_size=100), 
    input2=st.binary(min_size=1, max_size=100)
)
@pytest.mark.asyncio
async def test_property_2_isolation_guarantees(engine, registry, storage, input1, input2):
    """Property 2: Concurrent requests do not cross-contaminate."""
    # Register a generic mock model
    model_binary = b"concurrent_model_test"
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import os
    sealed_key = engine.sealed_key
    aesgcm = AESGCM(sealed_key)
    nonce = os.urandom(12)
    encrypted_data = nonce + aesgcm.encrypt(nonce, model_binary, None)

    cid = await storage.upload(encrypted_data)
    model_id = "test_model_prop2"
    await registry.register(model_id, cid, encrypted_data)

    req1 = InferenceRequest(model_id=model_id, input_data=input1)
    req2 = InferenceRequest(model_id=model_id, input_data=input2)

    # Fire concurrently
    receipt1, receipt2 = await asyncio.gather(
        engine.execute(req1),
        engine.execute(req2)
    )

    # Ensure output reflects the correct input strictly (our enclave sim echoes input)
    assert input1 in receipt1.output
    assert input2 in receipt2.output
    if input1 != input2:
        assert receipt1.output != receipt2.output

@settings(max_examples=10, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(model_id=st.text(min_size=1), input_data=st.binary(min_size=1))
@pytest.mark.asyncio
async def test_property_3_missing_model(engine, model_id, input_data):
    """Property 3: Missing model raises ModelNotFoundError and logs nothing."""
    req = InferenceRequest(model_id=model_id, input_data=input_data)
    
    with pytest.raises(ModelNotFoundError):
        await engine.execute(req)

    # Verify audit log is empty
    logs = await engine.redis.xread({"inference:audit_log": "0"})
    assert len(logs) == 0

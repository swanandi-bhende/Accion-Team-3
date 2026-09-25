import pytest
import pytest_asyncio
import base64
from httpx import AsyncClient, ASGITransport
import fakeredis.aioredis as fakeredis

import os
os.environ["TEE_MODE"] = "sgx"
os.environ["TEE_SEALED_KEY_PATH"] = "./data/sealed_key.bin"
os.environ["REDIS_URL"] = "redis://localhost:6379"
os.environ["MODEL_STORAGE_BACKEND"] = "ipfs"
os.environ["MODEL_STORAGE_URL"] = "http://localhost:5001"
os.environ["WEB3_WS_URL"] = "ws://localhost:8545"
os.environ["WEB3_RPC_URL"] = "http://localhost:8545"
os.environ["TASK_MANAGER_ADDRESS"] = "0x0000000000000000000000000000000000000000"
os.environ["ESCROW_ADDRESS"] = "0x0000000000000000000000000000000000000000"
os.environ["PROOF_SUBMITTER_PRIVATE_KEY"] = "0x0000000000000000000000000000000000000000000000000000000000000000"
os.environ["ZKML_PROVING_KEY_STORE"] = "./data/proving_keys"
os.environ["ZKML_SUPPORTED_ARCHITECTURES"] = "linear"
from inference.app import app
from common.model_registry import ModelRegistry
from tests.property.test_model_registry_prop import MockStorageBackend

@pytest_asyncio.fixture
async def override_deps():
    redis_client = fakeredis.FakeRedis(decode_responses=False)
    storage = MockStorageBackend()
    registry = ModelRegistry(redis_client, storage)
    
    from inference.tee_runtime import TEERuntime
    tee = TEERuntime("sgx")
    tee.initialize()
    
    from inference.engine import InferenceEngine
    engine = InferenceEngine(registry, tee, redis_client, timeout_s=2.0)
    
    app.state.redis_client = redis_client
    app.state.storage_backend = storage
    app.state.model_registry = registry
    app.state.tee_runtime = tee
    app.state.inference_engine = engine

    # We must seed a valid encrypted model for tests that expect 200 OK
    model_binary = b"api_test_model"
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import os
    aesgcm = AESGCM(engine.sealed_key)
    nonce = os.urandom(12)
    encrypted_data = nonce + aesgcm.encrypt(nonce, model_binary, None)

    cid = await storage.upload(encrypted_data)
    await registry.register("api_seeded_model", cid, encrypted_data)

    yield engine

@pytest.mark.asyncio
async def test_health_check(override_deps):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

@pytest.mark.asyncio
async def test_post_inference_success(override_deps):
    payload = {
        "model_id": "api_seeded_model",
        "input_data": base64.b64encode(b"test_input").decode("utf-8")
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/inference", json=payload)
    
    assert response.status_code == 200
    data = response.json()
    assert "execution_id" in data
    assert data["model_id"] == "api_seeded_model"

@pytest.mark.asyncio
async def test_post_inference_404_not_found(override_deps):
    payload = {
        "model_id": "nonexistent_model",
        "input_data": base64.b64encode(b"test_input").decode("utf-8")
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/inference", json=payload)
    
    assert response.status_code == 404
    assert response.json()["error"] == "ModelNotFound"

@pytest.mark.asyncio
async def test_post_inference_408_timeout(override_deps):
    engine = override_deps
    # Hack the TEE to sleep longer than the engine timeout (2.0s)
    import asyncio
    async def slow_isolated(*args, **kwargs):
        await asyncio.sleep(2.5)
        return b"slow"
    
    engine.tee.enclave.run_isolated = slow_isolated

    payload = {
        "model_id": "api_seeded_model",
        "input_data": base64.b64encode(b"test_input").decode("utf-8")
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/inference", json=payload)
    
    assert response.status_code == 408
    assert response.json()["error"] == "TimeoutExceeded"

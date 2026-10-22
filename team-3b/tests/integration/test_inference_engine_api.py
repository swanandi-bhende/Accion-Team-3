import base64
import os
from collections.abc import AsyncIterator
from typing import Any

import fakeredis.aioredis as fakeredis
import numpy as np
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

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
from common.model_registry import ModelRegistry
from common.tensors import decode_tensor
from inference.app import app
from inference.engine import InferenceEngine
from tests.onnx_fixtures import (
    expected_linear_output,
    linear_model_bytes,
    tensor,
    tensor_bytes,
)
from tests.property.test_model_registry_prop import MockStorageBackend


@pytest_asyncio.fixture
async def override_deps() -> AsyncIterator[InferenceEngine]:
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

    # A real ONNX model, registered as plaintext: the registry hashes the bytes it is
    # given, so an encrypted blob would be hashed as is and never decrypted.
    model_binary = linear_model_bytes()
    cid = await storage.upload(model_binary)
    await registry.register("api_seeded_model", cid, model_binary)

    yield engine

@pytest.mark.asyncio
async def test_health_check(override_deps: InferenceEngine) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

@pytest.mark.asyncio
async def test_post_inference_success(override_deps: InferenceEngine) -> None:
    payload = {
        "model_id": "api_seeded_model",
        "input_data": base64.b64encode(tensor_bytes()).decode("utf-8"),
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/inference", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert "execution_id" in data
    assert data["model_id"] == "api_seeded_model"
    output = decode_tensor(base64.b64decode(data["output"]))
    assert np.allclose(output, expected_linear_output(tensor()), atol=1e-5)

@pytest.mark.asyncio
async def test_post_inference_404_not_found(override_deps: InferenceEngine) -> None:
    payload = {
        "model_id": "nonexistent_model",
        "input_data": base64.b64encode(b"test_input").decode("utf-8")
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/inference", json=payload)

    assert response.status_code == 404
    assert response.json()["error"] == "ModelNotFound"

@pytest.mark.asyncio
async def test_post_inference_408_timeout(override_deps: InferenceEngine) -> None:
    engine = override_deps
    # Hack the TEE to sleep longer than the engine timeout (2.0s)
    import asyncio
    async def slow_isolated(*args: Any, **kwargs: Any) -> bytes:
        await asyncio.sleep(2.5)
        return b"slow"

    assert engine.tee.enclave is not None
    engine.tee.enclave.run_isolated = slow_isolated  # type: ignore[method-assign]

    payload = {
        "model_id": "api_seeded_model",
        "input_data": base64.b64encode(b"test_input").decode("utf-8")
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/inference", json=payload)

    assert response.status_code == 408
    assert response.json()["error"] == "TimeoutExceeded"

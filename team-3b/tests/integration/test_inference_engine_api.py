import base64
import os
from collections.abc import AsyncIterator
from typing import Any

import fakeredis.aioredis as fakeredis
import numpy as np
import pytest
import pytest_asyncio
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_account.signers.local import LocalAccount
from httpx import ASGITransport, AsyncClient

os.environ["TEE_MODE"] = "sgx"
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
from inference.auth import Challenges
from inference.engine import InferenceEngine
from tests.onnx_fixtures import (
    expected_linear_output,
    linear_model_bytes,
    tensor,
    tensor_bytes,
)
from tests.property.test_model_registry_prop import MockStorageBackend

TASK_ID = "0x" + "ab" * 32
# Test-only keys. The worker of TASK_ID is WALLET; OTHER is a wallet that is not.
WALLET = Account.from_key("0x" + "11" * 32)
OTHER = Account.from_key("0x" + "22" * 32)


async def signed_call(
    model_id: str | None = "api_seeded_model",
    model_cid: str | None = None,
    input_data: bytes | None = None,
    account: LocalAccount = WALLET,
    signer: LocalAccount | None = None,
) -> dict[str, Any]:
    """An /inference body with a challenge from the service, signed by ``signer`` (default ``account``)."""
    challenge = await app.state.challenges.issue(TASK_ID, account.address)
    signature = (signer or account).sign_message(encode_defunct(text=challenge["message"])).signature
    body: dict[str, Any] = {
        "task_id": TASK_ID,
        "input_data": base64.b64encode(tensor_bytes() if input_data is None else input_data).decode("utf-8"),
        "authorization": {
            "wallet_address": account.address,
            "nonce": challenge["nonce"],
            "expires_at": challenge["expires_at"],
            "signature": "0x" + bytes(signature).hex(),
        },
    }
    if model_cid is not None:
        body["model_cid"] = model_cid
    else:
        body["model_id"] = model_id
    return body


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

    workers = {TASK_ID: WALLET.address}

    async def task_worker(task_id: str) -> str | None:
        return workers.get(task_id)

    app.state.redis_client = redis_client
    app.state.storage_backend = storage
    app.state.model_registry = registry
    app.state.tee_runtime = tee
    app.state.inference_engine = engine
    app.state.challenges = Challenges(redis_client, task_worker)

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
    payload = await signed_call()
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
    payload = await signed_call(model_id="nonexistent_model", input_data=b"test_input")
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

    payload = await signed_call(input_data=b"test_input")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/inference", json=payload)

    assert response.status_code == 408
    assert response.json()["error"] == "TimeoutExceeded"

@pytest.mark.asyncio
async def test_post_inference_without_authorization_is_rejected(override_deps: InferenceEngine) -> None:
    payload = await signed_call()
    del payload["authorization"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/inference", json=payload)

    assert response.status_code == 422

@pytest.mark.asyncio
async def test_post_inference_from_a_wallet_that_is_not_the_worker_is_403(override_deps: InferenceEngine) -> None:
    payload = await signed_call(account=OTHER)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/inference", json=payload)

    assert response.status_code == 403
    assert response.json()["error"] == "not_task_worker"

@pytest.mark.asyncio
async def test_post_inference_signed_by_another_wallet_is_401(override_deps: InferenceEngine) -> None:
    payload = await signed_call(account=WALLET, signer=OTHER)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/inference", json=payload)

    assert response.status_code == 401
    assert response.json()["error"] == "bad_signature"

@pytest.mark.asyncio
async def test_a_challenge_gives_one_inference(override_deps: InferenceEngine) -> None:
    payload = await signed_call()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        first = await ac.post("/inference", json=payload)
        second = await ac.post("/inference", json=payload)

    assert first.status_code == 200
    assert second.status_code == 401
    assert second.json()["error"] == "challenge_unknown"

@pytest.mark.asyncio
async def test_post_inference_by_cid_resolves_the_current_version(override_deps: InferenceEngine) -> None:
    entry = await app.state.model_registry.get("api_seeded_model")
    payload = await signed_call(model_cid=entry.cid)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/inference", json=payload)

    assert response.status_code == 200
    assert response.json()["model_id"] == "api_seeded_model"

@pytest.mark.asyncio
async def test_an_unknown_cid_is_404_only_after_authorization(override_deps: InferenceEngine) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        authorized = await ac.post("/inference", json=await signed_call(model_cid="QmUnknown"))
        unauthorized = await ac.post("/inference", json=await signed_call(model_cid="QmUnknown", signer=OTHER))

    assert authorized.status_code == 404
    assert authorized.json()["error"] == "ModelNotFound"
    # An unauthorized caller gets the authorization error, so it cannot probe for CIDs.
    assert unauthorized.status_code == 401
    assert unauthorized.json()["error"] == "bad_signature"

@pytest.mark.asyncio
async def test_naming_no_model_or_two_is_422(override_deps: InferenceEngine) -> None:
    neither = await signed_call(model_id=None)
    both = {**await signed_call(model_cid="QmUnknown"), "model_id": "api_seeded_model"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        no_model = await ac.post("/inference", json=neither)
        two_models = await ac.post("/inference", json=both)

    assert no_model.status_code == 422
    assert two_models.status_code == 422

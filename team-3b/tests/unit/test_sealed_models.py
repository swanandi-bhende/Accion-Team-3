import hashlib
from pathlib import Path

import fakeredis.aioredis as fakeredis
import numpy as np
import pytest

from common.exceptions import ConfigurationError, IntegrityCheckFailedError
from common.model_registry import ModelRegistry
from common.models import InferenceRequest
from common.sealing import seal
from common.tensors import decode_tensor
from inference.engine import InferenceEngine
from inference.sealed_key import load_sealed_key
from inference.tee_runtime import TEERuntime
from tests.onnx_fixtures import (
    expected_linear_output,
    linear_model_bytes,
    tensor,
    tensor_bytes,
)
from tests.property.test_model_registry_prop import MockStorageBackend

PROVISIONED_KEY = bytes(range(32))


@pytest.fixture
def setup(tmp_path: Path) -> tuple[InferenceEngine, ModelRegistry, fakeredis.FakeRedis, TEERuntime]:
    redis_client = fakeredis.FakeRedis(decode_responses=False)
    registry = ModelRegistry(redis_client, MockStorageBackend(), spool_dir=tmp_path)
    tee = TEERuntime("sgx")
    tee.initialize()
    engine = InferenceEngine(registry, tee, redis_client, timeout_s=10.0, sealed_key=PROVISIONED_KEY)
    return engine, registry, redis_client, tee


async def publish_sealed(
    registry: ModelRegistry, model_id: str, plaintext: bytes, key: bytes = PROVISIONED_KEY
) -> None:
    """Upload the sealed blob, registering the hash of the plaintext the TEE will check."""
    await registry.upload_model(
        model_id,
        seal(plaintext, key),
        architecture="linear",
        model_hash=hashlib.sha256(plaintext).hexdigest(),
    )


@pytest.mark.asyncio
async def test_sealed_model_runs_with_the_provisioned_key(
    setup: tuple[InferenceEngine, ModelRegistry, fakeredis.FakeRedis, TEERuntime],
) -> None:
    engine, registry, _, _ = setup
    await publish_sealed(registry, "sealed-linear", linear_model_bytes(3))

    receipt = await engine.execute(
        InferenceRequest(model_id="sealed-linear", input_data=tensor_bytes(seed=4))
    )

    assert np.allclose(decode_tensor(receipt.output), expected_linear_output(tensor(seed=4), 3), atol=1e-5)


@pytest.mark.asyncio
async def test_stored_artifact_is_the_sealed_blob_not_the_plaintext(
    setup: tuple[InferenceEngine, ModelRegistry, fakeredis.FakeRedis, TEERuntime],
) -> None:
    _, registry, _, _ = setup
    plaintext = linear_model_bytes(3)
    await publish_sealed(registry, "sealed-at-rest", plaintext)

    entry = await registry.get("sealed-at-rest")
    assert entry is not None
    stored = await registry.storage.retrieve(entry.cid)
    assert stored != plaintext
    assert entry.hash == hashlib.sha256(plaintext).hexdigest()


@pytest.mark.asyncio
async def test_blob_sealed_under_another_key_is_refused(
    setup: tuple[InferenceEngine, ModelRegistry, fakeredis.FakeRedis, TEERuntime],
) -> None:
    engine, registry, _, _ = setup
    await publish_sealed(registry, "wrong-key", linear_model_bytes(), key=bytes(32))

    with pytest.raises(IntegrityCheckFailedError):
        await engine.execute(InferenceRequest(model_id="wrong-key", input_data=tensor_bytes()))


@pytest.mark.asyncio
async def test_blob_that_opens_to_other_bytes_is_refused(
    setup: tuple[InferenceEngine, ModelRegistry, fakeredis.FakeRedis, TEERuntime],
) -> None:
    engine, registry, _, _ = setup
    registered_plaintext = linear_model_bytes(0)
    await registry.upload_model(
        "swapped",
        seal(linear_model_bytes(9), PROVISIONED_KEY),
        architecture="linear",
        model_hash=hashlib.sha256(registered_plaintext).hexdigest(),
    )

    with pytest.raises(IntegrityCheckFailedError):
        await engine.execute(InferenceRequest(model_id="swapped", input_data=tensor_bytes()))


@pytest.mark.asyncio
async def test_engine_without_the_provisioned_key_cannot_open_the_model(
    setup: tuple[InferenceEngine, ModelRegistry, fakeredis.FakeRedis, TEERuntime],
) -> None:
    _, registry, redis_client, tee = setup
    await publish_sealed(registry, "needs-the-key", linear_model_bytes())
    unprovisioned = InferenceEngine(registry, tee, redis_client, timeout_s=10.0)

    with pytest.raises(IntegrityCheckFailedError):
        await unprovisioned.execute(InferenceRequest(model_id="needs-the-key", input_data=tensor_bytes()))


def test_sealed_key_is_created_once_and_reloaded_after_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "keys" / "sealed_key.bin"

    first = load_sealed_key(str(path))
    after_restart = load_sealed_key(str(path))

    assert first is not None and len(first) == 32
    assert after_restart == first
    assert path.stat().st_mode & 0o777 == 0o600


def test_no_key_path_gives_no_key_and_a_wrong_length_file_is_refused(tmp_path: Path) -> None:
    assert load_sealed_key(None) is None
    short = tmp_path / "short.bin"
    short.write_bytes(b"x" * 16)

    with pytest.raises(ConfigurationError, match="exactly 32 raw bytes"):
        load_sealed_key(str(short))

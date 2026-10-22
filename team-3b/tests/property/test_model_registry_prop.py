import asyncio
import hashlib
import tempfile
from pathlib import Path
from unittest.mock import patch

import fakeredis.aioredis as fakeredis
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from redis.asyncio import Redis

from common.exceptions import IntegrityCheckFailedError, StorageUnavailableError
from common.interfaces import StorageBackend
from common.model_registry import ModelRegistry


class MockStorageBackend(StorageBackend):
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    async def upload(self, data: bytes) -> str:
        cid = f"Qm{hashlib.sha256(data).hexdigest()[:20]}"
        self.store[cid] = data
        return cid

    async def retrieve(self, cid: str) -> bytes:
        if cid not in self.store:
            raise Exception("Not found")
        return self.store[cid]

@pytest.fixture
def redis_client() -> Redis:
    return fakeredis.FakeRedis(decode_responses=False)

@pytest.fixture
def storage() -> MockStorageBackend:
    return MockStorageBackend()

@pytest.fixture
def registry(redis_client: Redis, storage: MockStorageBackend) -> ModelRegistry:
    return ModelRegistry(redis_client, storage)

@settings(max_examples=25)
@given(model_id=st.text(min_size=1, max_size=20), model_binary=st.binary(min_size=1, max_size=100))
@pytest.mark.asyncio
async def test_property_19_model_registry_round_trip(model_id: str, model_binary: bytes) -> None:
    """Property 19: Registering and querying returns exact CID."""
    redis_client = fakeredis.FakeRedis(decode_responses=False)
    storage = MockStorageBackend()
    registry = ModelRegistry(redis_client, storage)

    cid = await storage.upload(model_binary)
    await registry.register(model_id, cid, model_binary)

    # Query without version (latest)
    entry = await registry.get(model_id)
    assert entry is not None
    assert entry.cid == cid
    assert entry.model_id == model_id

    # Query with version
    entry_v = await registry.get(model_id, version=entry.version)
    assert entry_v is not None
    assert entry_v.cid == cid



@settings(max_examples=25)
@given(model_id=st.text(min_size=1, max_size=20), model_binary=st.binary(min_size=1, max_size=100))
@pytest.mark.asyncio
async def test_property_20_cid_validation_before_reference(model_id: str, model_binary: bytes) -> None:
    """Property 20: A CID must resolve to exactly the expected model bytes."""
    redis_client = fakeredis.FakeRedis(decode_responses=False)
    storage = MockStorageBackend()
    registry = ModelRegistry(redis_client, storage)

    invalid_cid = "QmInvalid"

    with pytest.raises(IntegrityCheckFailedError):
        await registry.register(model_id, invalid_cid, model_binary)

    valid_cid = await storage.upload(model_binary)
    storage.store[valid_cid] = b"tampered-content"

    with pytest.raises(IntegrityCheckFailedError):
        await registry.register(model_id, valid_cid, model_binary)

    assert await registry.get(model_id) is None



class FlakyStorage(MockStorageBackend):
    """Fails the first ``fail_count`` uploads with an outage, then behaves normally."""

    def __init__(self, fail_count: int) -> None:
        super().__init__()
        self.fail_count = fail_count

    async def upload(self, data: bytes) -> str:
        if self.fail_count > 0:
            self.fail_count -= 1
            raise StorageUnavailableError("simulated outage")
        return await super().upload(data)


@settings(max_examples=50, deadline=None)
@given(fail_count=st.integers(min_value=0, max_value=20))
def test_property_15_exponential_backoff_bounds(fail_count: int) -> None:
    """Property 15: retries wait 5 s, double each time, and never wait more than 300 s (Requirement 7.5).

    The expected delays are the spec's numbers, not the registry's constants, so the test
    fails if either the starting delay or the cap changes.
    """
    delays: list[float] = []

    async def record(delay: float) -> None:
        delays.append(delay)

    async def upload() -> None:
        with tempfile.TemporaryDirectory() as spool, patch("common.model_registry.asyncio.sleep", record):
            registry = ModelRegistry(fakeredis.FakeRedis(decode_responses=False), FlakyStorage(fail_count), spool_dir=Path(spool))
            await registry.upload_model("prop_model", b"model-bytes")

    asyncio.run(upload())
    assert delays == [min(5.0 * 2**n, 300.0) for n in range(fail_count)]

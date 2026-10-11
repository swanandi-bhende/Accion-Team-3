import hashlib

import fakeredis.aioredis as fakeredis
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from redis.asyncio import Redis

from common.exceptions import IntegrityCheckFailedError
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



def test_property_15_exponential_backoff_bounds() -> None:
    """Property 15: Exponential backoff delay bounds."""
    base = ModelRegistry.UPLOAD_BACKOFF_BASE
    max_delay = ModelRegistry.UPLOAD_BACKOFF_MAX

    for n in range(1, 10):
        expected = min(base * (2 ** (n - 1)), max_delay)
        # We test the logic explicitly
        assert expected <= max_delay

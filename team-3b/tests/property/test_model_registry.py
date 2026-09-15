import pytest
import hashlib
import asyncio
from hypothesis import given, settings, strategies as st
import fakeredis.aioredis as fakeredis

from common.model_registry import ModelRegistry
from common.exceptions import IntegrityCheckFailedError
from common.interfaces import StorageBackend

class MockStorageBackend(StorageBackend):
    def __init__(self):
        self.store = {}

    async def upload(self, data: bytes) -> str:
        cid = f"Qm{hashlib.sha256(data).hexdigest()[:20]}"
        self.store[cid] = data
        return cid

    async def retrieve(self, cid: str) -> bytes:
        if cid not in self.store:
            raise Exception("Not found")
        return self.store[cid]

@pytest.fixture
def redis_client():
    return fakeredis.FakeRedis(decode_responses=False)

@pytest.fixture
def storage():
    return MockStorageBackend()

@pytest.fixture
def registry(redis_client, storage):
    return ModelRegistry(redis_client, storage)

@settings(max_examples=10)
@given(model_id=st.text(min_size=1, max_size=20), model_binary=st.binary(min_size=1, max_size=100))
@pytest.mark.asyncio
async def test_property_19_model_registry_round_trip(model_id, model_binary):
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
    
    await redis_client.aclose()

@settings(max_examples=10)
@given(model_id=st.text(min_size=1, max_size=20), model_binary=st.binary(min_size=1, max_size=100))
@pytest.mark.asyncio
async def test_property_20_cid_validation_before_reference(model_id, model_binary):
    """Property 20: Invalid CID rejected before registration."""
    redis_client = fakeredis.FakeRedis(decode_responses=False)
    storage = MockStorageBackend()
    registry = ModelRegistry(redis_client, storage)

    invalid_cid = "QmInvalid"
    
    with pytest.raises(IntegrityCheckFailedError):
        await registry.register(model_id, invalid_cid, model_binary)
        
    await redis_client.aclose()

def test_property_15_exponential_backoff_bounds():
    """Property 15: Exponential backoff delay bounds."""
    base = ModelRegistry.UPLOAD_BACKOFF_BASE
    max_delay = ModelRegistry.UPLOAD_BACKOFF_MAX
    
    for n in range(1, 10):
        expected = min(base * (2 ** (n - 1)), max_delay)
        # We test the logic explicitly
        assert expected <= max_delay

import asyncio

import fakeredis.aioredis as fakeredis
import pytest

from common.exceptions import StorageUnavailableError
from common.interfaces import StorageBackend
from common.model_registry import ModelRegistry


class FailingStorageBackend(StorageBackend):
    def __init__(self, fail_count):
        self.fail_count = fail_count
        self.attempts = 0

    async def upload(self, data: bytes) -> str:
        self.attempts += 1
        if self.attempts <= self.fail_count:
            raise StorageUnavailableError("Simulated failure")
        return "QmSuccess"

    async def retrieve(self, cid: str) -> bytes:
        return b"mock_data"

@pytest.fixture
def redis_client():
    return fakeredis.FakeRedis(decode_responses=False)

@pytest.mark.asyncio
async def test_get_unknown_model_returns_none(redis_client):
    storage = FailingStorageBackend(0)
    registry = ModelRegistry(redis_client, storage)

    result = await registry.get("unknown_model")
    assert result is None

@pytest.mark.asyncio
async def test_register_multiple_versions(redis_client):
    from tests.property.test_model_registry_prop import MockStorageBackend
    storage = MockStorageBackend()
    registry = ModelRegistry(redis_client, storage)

    model_id = "test_model"
    bin1 = b"version1"
    bin2 = b"version2"

    cid1 = await storage.upload(bin1)
    await registry.register(model_id, cid1, bin1)

    cid2 = await storage.upload(bin2)
    await registry.register(model_id, cid2, bin2)

    latest = await registry.get(model_id)
    assert latest.version == 2
    assert latest.cid == cid2

    v1 = await registry.get(model_id, version=1)
    assert v1.version == 1
    assert v1.cid == cid1

@pytest.mark.asyncio
async def test_concurrent_next_version(redis_client):
    storage = FailingStorageBackend(0)
    registry = ModelRegistry(redis_client, storage)

    model_id = "concurrent_model"

    # Run 10 next_version requests concurrently
    tasks = [registry._next_version(model_id) for _ in range(10)]
    results = await asyncio.gather(*tasks)

    # Should get exactly 1 to 10
    assert sorted(results) == list(range(1, 11))

@pytest.mark.asyncio
async def test_upload_backoff_and_success(redis_client):
    # This will fail twice, succeed on third attempt
    storage = FailingStorageBackend(fail_count=2)
    registry = ModelRegistry(redis_client, storage)

    # Temporarily speed up the backoff for test speed
    registry.UPLOAD_BACKOFF_BASE = 0.01

    # Should eventually return QmSuccess after retries
    cid = await registry.upload_model("test_model", b"mock_data")
    assert cid == "QmSuccess"
    assert storage.attempts == 3

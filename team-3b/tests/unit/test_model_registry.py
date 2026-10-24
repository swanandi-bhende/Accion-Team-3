import asyncio
import hashlib
from collections.abc import Awaitable
from pathlib import Path
from typing import cast

import fakeredis.aioredis as fakeredis
import pytest

from common.exceptions import IntegrityCheckFailedError, StorageUnavailableError
from common.interfaces import StorageBackend
from common.model_registry import FAILED_UPLOAD_KEY, UPLOAD_QUEUE_KEY, ModelRegistry
from common.sealing import seal
from tests.property.test_model_registry_prop import MockStorageBackend


async def queued_uploads(registry: ModelRegistry) -> int:
    """How many uploads are still queued in Redis, waiting for storage."""
    return int(await cast(Awaitable[int], registry.redis.llen(UPLOAD_QUEUE_KEY)))


class FailingStorageBackend(StorageBackend):
    def __init__(self, fail_count: int) -> None:
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
def redis_client() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(decode_responses=False)

@pytest.mark.asyncio
async def test_get_unknown_model_returns_none(redis_client: fakeredis.FakeRedis) -> None:
    storage = FailingStorageBackend(0)
    registry = ModelRegistry(redis_client, storage)

    result = await registry.get("unknown_model")
    assert result is None

@pytest.mark.asyncio
async def test_register_multiple_versions(redis_client: fakeredis.FakeRedis) -> None:
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
    assert latest is not None
    assert latest.version == 2
    assert latest.cid == cid2

    v1 = await registry.get(model_id, version=1)
    assert v1 is not None
    assert v1.version == 1
    assert v1.cid == cid1

@pytest.mark.asyncio
async def test_concurrent_next_version(redis_client: fakeredis.FakeRedis) -> None:
    storage = FailingStorageBackend(0)
    registry = ModelRegistry(redis_client, storage)

    model_id = "concurrent_model"

    # Run 10 next_version requests concurrently
    tasks = [registry._next_version(model_id) for _ in range(10)]
    results = await asyncio.gather(*tasks)

    # Should get exactly 1 to 10
    assert sorted(results) == list(range(1, 11))

@pytest.mark.asyncio
async def test_upload_backoff_and_success(redis_client: fakeredis.FakeRedis) -> None:
    # This will fail twice, succeed on third attempt
    storage = FailingStorageBackend(fail_count=2)
    registry = ModelRegistry(redis_client, storage)

    # Temporarily speed up the backoff for test speed
    registry.UPLOAD_BACKOFF_BASE = 0.01

    # Should eventually return QmSuccess after retries
    cid = await registry.upload_model("test_model", b"mock_data")
    assert cid == "QmSuccess"
    assert storage.attempts == 3


@pytest.mark.asyncio
async def test_upload_waits_out_an_outage_with_no_attempt_cap(
    redis_client: fakeredis.FakeRedis, tmp_path: Path
) -> None:
    storage = FailingStorageBackend(fail_count=12)
    registry = ModelRegistry(redis_client, storage, spool_dir=tmp_path)
    registry.UPLOAD_BACKOFF_BASE = 0.001

    cid = await registry.upload_model("test_model", b"mock_data")

    assert cid == "QmSuccess"
    assert storage.attempts == 13
    assert await queued_uploads(registry) == 0
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_backoff_follows_the_spec_sequence_and_stops_at_the_maximum(
    monkeypatch: pytest.MonkeyPatch, redis_client: fakeredis.FakeRedis, tmp_path: Path
) -> None:
    """Requirement 7.5 with the default bounds: 5 s doubling to 300 s."""
    delays: list[float] = []

    async def record(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("common.model_registry.asyncio.sleep", record)
    registry = ModelRegistry(redis_client, FailingStorageBackend(fail_count=8), spool_dir=tmp_path)

    await registry.upload_model("test_model", b"mock_data")

    assert delays == [5.0, 10.0, 20.0, 40.0, 80.0, 160.0, 300.0, 300.0]


@pytest.mark.asyncio
async def test_queued_upload_survives_a_restart(redis_client: fakeredis.FakeRedis, tmp_path: Path) -> None:
    down = FailingStorageBackend(fail_count=10**9)
    first = ModelRegistry(redis_client, down, spool_dir=tmp_path)
    first.UPLOAD_BACKOFF_BASE = 0.001
    task = asyncio.create_task(first.upload_model("queued_model", b"model-bytes", architecture="linear"))
    for _ in range(500):
        if await queued_uploads(first) == 1:
            break
        await asyncio.sleep(0.005)
    assert await queued_uploads(first) == 1

    task.cancel()  # the process dies while storage is still down
    with pytest.raises(asyncio.CancelledError):
        await task

    restarted = ModelRegistry(redis_client, MockStorageBackend(), spool_dir=tmp_path)
    cids = await restarted.resume_uploads()

    entry = await restarted.get("queued_model")
    assert entry is not None
    assert cids == [entry.cid]
    assert entry.architecture == "linear"
    assert entry.hash == hashlib.sha256(b"model-bytes").hexdigest()
    assert await queued_uploads(restarted) == 0
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_integrity_failure_moves_the_upload_to_the_failed_list(
    redis_client: fakeredis.FakeRedis, tmp_path: Path
) -> None:
    class TamperedStorage(StorageBackend):
        async def upload(self, data: bytes) -> str:
            return "QmTampered"

        async def retrieve(self, cid: str) -> bytes:
            return b"not what was uploaded"

    registry = ModelRegistry(redis_client, TamperedStorage(), spool_dir=tmp_path)

    with pytest.raises(IntegrityCheckFailedError):
        await registry.upload_model("tampered", b"real-model")
    assert await queued_uploads(registry) == 0
    assert await cast(Awaitable[int], redis_client.llen(FAILED_UPLOAD_KEY)) == 1


@pytest.mark.asyncio
async def test_outage_during_validation_is_retried_not_treated_as_tampering(
    redis_client: fakeredis.FakeRedis, tmp_path: Path
) -> None:
    class FlakyRead(StorageBackend):
        def __init__(self) -> None:
            self.reads = 0

        async def upload(self, data: bytes) -> str:
            return "QmFlaky"

        async def retrieve(self, cid: str) -> bytes:
            self.reads += 1
            if self.reads < 3:
                raise StorageUnavailableError("read timed out")
            return b"good-model"

    storage = FlakyRead()
    registry = ModelRegistry(redis_client, storage, spool_dir=tmp_path)
    registry.UPLOAD_BACKOFF_BASE = 0.001

    cid = await registry.upload_model("flaky", b"good-model")

    assert cid == "QmFlaky"
    assert storage.reads == 3


@pytest.mark.asyncio
async def test_sealed_blob_is_registered_under_its_plaintext_hash(
    redis_client: fakeredis.FakeRedis, tmp_path: Path
) -> None:
    plaintext = b"model-weights"
    blob = seal(plaintext, bytes(range(32)))
    storage = MockStorageBackend()
    registry = ModelRegistry(redis_client, storage, spool_dir=tmp_path)

    cid = await registry.upload_model("sealed", blob, model_hash=hashlib.sha256(plaintext).hexdigest())

    entry = await registry.get("sealed")
    assert entry is not None
    assert entry.cid == cid
    assert entry.hash == hashlib.sha256(plaintext).hexdigest()
    assert await storage.retrieve(cid) == blob

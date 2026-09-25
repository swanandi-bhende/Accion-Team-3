import asyncio
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis as fakeredis
import pytest

from common.exceptions import TimeoutExceededError
from common.model_registry import ModelRegistry
from common.models import InferenceRequest
from inference.engine import InferenceEngine
from inference.tee_runtime import TEERuntime
from tests.property.test_model_registry_prop import MockStorageBackend


def build_engine(timeout_s=30.0):
    redis_client = fakeredis.FakeRedis(decode_responses=False)
    storage = MockStorageBackend()
    registry = ModelRegistry(redis_client, storage)
    tee = TEERuntime("sgx")
    tee.initialize()
    return InferenceEngine(registry, tee, redis_client, timeout_s=timeout_s), registry, storage


@pytest.mark.asyncio
async def test_empty_input_data_produces_a_receipt():
    engine, registry, storage = build_engine()
    model_binary = b"empty-input-model"
    cid = await storage.upload(model_binary)
    await registry.register("model:with spaces/and:punctuation", cid, model_binary)

    receipt = await engine.execute(
        InferenceRequest(model_id="model:with spaces/and:punctuation", input_data=b"")
    )

    assert receipt.input_hash
    assert receipt.output


@pytest.mark.asyncio
async def test_unusual_model_id_is_stored_and_resolved_exactly():
    engine, registry, storage = build_engine()
    model_id = "../model%{hash}[0]:unicode-like"
    model_binary = b"unusual-id-model"
    cid = await storage.upload(model_binary)

    await registry.register(model_id, cid, model_binary)
    entry = await registry.get(model_id)

    assert entry is not None
    assert entry.model_id == model_id


@pytest.mark.asyncio
async def test_timeout_boundary_uses_exactly_30_seconds():
    engine, registry, storage = build_engine(timeout_s=30.0)
    model_binary = b"timeout-boundary-model"
    cid = await storage.upload(model_binary)
    await registry.register("timeout-boundary", cid, model_binary)

    wait_for = asyncio.wait_for

    async def record_wait_for(awaitable, timeout):
        assert timeout == 30.0
        return await awaitable

    with patch("inference.engine.asyncio.wait_for", side_effect=record_wait_for):
        receipt = await engine.execute(
            InferenceRequest(model_id="timeout-boundary", input_data=b"input")
        )

    assert receipt.model_id == "timeout-boundary"


@pytest.mark.asyncio
async def test_timeout_error_branch_is_raised():
    engine, registry, storage = build_engine(timeout_s=30.0)
    model_binary = b"timeout-model"
    cid = await storage.upload(model_binary)
    await registry.register("timeout-model", cid, model_binary)
    engine.tee.execute_inference = AsyncMock(side_effect=asyncio.TimeoutError)

    with pytest.raises(TimeoutExceededError):
        await engine.execute(InferenceRequest(model_id="timeout-model", input_data=b"x"))


@pytest.mark.asyncio
async def test_redis_reconnects_after_client_close():
    redis_client = __import__("redis.asyncio", fromlist=["Redis"]).Redis.from_url(
        "redis://localhost:6379", decode_responses=False
    )
    try:
        try:
            await redis_client.ping()
        except Exception:
            pytest.skip("Redis service is not available")

        storage = MockStorageBackend()
        registry = ModelRegistry(redis_client, storage)
        model_binary = b"restart-resilience-model"
        cid = await storage.upload(model_binary)
        await registry.register("restart-resilience", cid, model_binary)

        await redis_client.close()
        redis_client = __import__("redis.asyncio", fromlist=["Redis"]).Redis.from_url(
            "redis://localhost:6379", decode_responses=False
        )
        registry.redis = redis_client

        entry = await registry.get("restart-resilience")
        assert entry is not None
        assert entry.cid == cid
    finally:
        await redis_client.close()
import pytest
import redis.asyncio as redis

from event_listener.listener import EventListener
from proof_submitter import ProofSubmitter


@pytest.mark.asyncio
async def test_wave2_persistence_and_dlq_use_real_redis_when_available():
    client = redis.from_url("redis://localhost:6379", decode_responses=False)
    try:
        try:
            await client.ping()
        except Exception:
            pytest.skip("Redis service is not available")

        listener = EventListener(None, None, None, client)
        await listener._persist_block_number(123)
        assert await listener._get_last_processed_block() == 123

        submitter = ProofSubmitter(None, None, client)
        await submitter._persist_to_dlq([], RuntimeError("real redis check"))
        entry = await client.lindex(submitter.DLQ_KEY, 0)
        assert entry is not None
        await client.delete(submitter.DLQ_KEY, listener.LAST_BLOCK_KEY)
    finally:
        await client.close()
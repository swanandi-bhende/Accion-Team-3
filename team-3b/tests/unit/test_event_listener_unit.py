from types import SimpleNamespace

import fakeredis.aioredis as fakeredis
import pytest
from httpx import ASGITransport, AsyncClient

from event_listener.app import create_status_app
from event_listener.config import load_contract_abi
from event_listener.listener import EventListener


def make_listener(redis=None):
    return EventListener(
        web3=SimpleNamespace(),
        task_manager=SimpleNamespace(),
        escrow=SimpleNamespace(),
        redis_client=redis or fakeredis.FakeRedis(decode_responses=False),
    )


def test_mock_contract_abis_load_from_hardhat_artifacts():
    task_abi = load_contract_abi("TaskManager")
    escrow_abi = load_contract_abi("Escrow")

    assert any(item.get("name") == "TaskCreated" for item in task_abi)
    assert any(item.get("name") == "TaskFunded" for item in escrow_abi)


@pytest.mark.asyncio
async def test_task_state_survives_listener_restart():
    redis = fakeredis.FakeRedis(decode_responses=False)
    first = make_listener(redis)
    first._task_events["task"] = {"funded": {"funded_amount": 1}}
    first._pending_since["task"] = first.clock()
    await first._persist_task_state("task")

    second = make_listener(redis)
    await second._restore_task_states()

    assert second._task_events["task"]["funded"]["funded_amount"] == 1


@pytest.mark.asyncio
async def test_status_endpoint_reports_listener_state():
    listener = make_listener()
    await listener._persist_block_number(42)
    listener._connection_state = "connected"
    app = create_status_app(listener)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/listener/status")

    assert response.status_code == 200
    assert response.json() == {
        "last_processed_block": 42,
        "pending_tasks": 0,
        "connection_state": "connected",
    }
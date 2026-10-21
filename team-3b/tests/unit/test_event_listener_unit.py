import asyncio
from types import SimpleNamespace

import fakeredis.aioredis as fakeredis
import pytest
from httpx import ASGITransport, AsyncClient

from common.config import get_settings
from event_listener.app import create_status_app
from event_listener.config import load_contract_abi
from event_listener.listener import EventListener


def make_listener(redis: fakeredis.FakeRedis | None = None) -> EventListener:
    return EventListener(
        web3=SimpleNamespace(),
        task_manager=SimpleNamespace(),
        escrow=SimpleNamespace(),
        redis_client=redis or fakeredis.FakeRedis(decode_responses=False),
    )


def test_team_a_contract_abis_load_from_the_default_artifacts_dir() -> None:
    """The shipped defaults must resolve Team A's exports with no configuration."""
    settings = get_settings()
    task_abi = load_contract_abi(settings.task_manager_artifact)
    escrow_abi = load_contract_abi(settings.escrow_artifact)

    assert any(item.get("name") == "TaskCreated" for item in task_abi)
    assert any(item.get("name") == "TaskFunded" for item in escrow_abi)


@pytest.mark.asyncio
async def test_task_state_survives_listener_restart() -> None:
    redis = fakeredis.FakeRedis(decode_responses=False)
    first = make_listener(redis)
    first._task_events["task"] = {"funded": {"funded_amount": 1}}
    first._pending_since["task"] = first.clock()
    await first._persist_task_state("task")

    second = make_listener(redis)
    await second._restore_task_states()

    assert second._task_events["task"]["funded"]["funded_amount"] == 1


@pytest.mark.asyncio
async def test_stop_returns_from_start_after_the_current_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    listener = make_listener()
    passes: list[int] = []

    async def record_pass(from_block: int) -> None:
        passes.append(from_block)

    async def stop_during_first_sleep(delay: float) -> None:
        listener.stop()

    monkeypatch.setattr(listener, "_process_events", record_pass)
    listener.sleep = stop_during_first_sleep

    await asyncio.wait_for(listener.start(poll_interval=0.01), timeout=1.0)

    assert len(passes) == 1


@pytest.mark.asyncio
async def test_status_endpoint_reports_listener_state() -> None:
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

import asyncio
import json
import time
from types import SimpleNamespace
from typing import Any

import fakeredis.aioredis as fakeredis
import pytest
from httpx import ASGITransport, AsyncClient

from common.config import get_settings
from event_listener.app import create_status_app
from event_listener.config import load_contract_abi
from event_listener.listener import EventListener


async def ready_task(listener: EventListener, task_id: str) -> None:
    """Record both events for ``task_id`` so the task is ready to dispatch."""
    deadline = int(time.time()) + 3600
    listener._task_events[task_id] = {
        "created": {"task_id": task_id, "deadline": deadline, "description_hash": b"\x01", "block_number": 1},
        "funded": {"task_id": task_id, "funded_amount": 1, "block_number": 2},
    }
    await listener._persist_task_state(task_id)


GENESIS_A = "0x" + "aa" * 32
GENESIS_B = "0x" + "bb" * 32


class FakeChain:
    """The part of web3 the listener reads to tell which chain it follows."""

    def __init__(self, genesis: str, head: int) -> None:
        self.genesis = genesis
        self.eth = SimpleNamespace(block_number=head, get_block=self._get_block)

    def _get_block(self, number: int) -> dict[str, bytes]:
        assert number == 0
        return {"hash": bytes.fromhex(self.genesis.removeprefix("0x"))}


class FakeEvents:
    """A contract event whose get_logs returns the entries at or after from_block."""

    def __init__(self, entries: list[dict[str, Any]] | None = None) -> None:
        self.entries = entries or []
        self.calls: list[tuple[int, Any]] = []

    def get_logs(self, from_block: int, to_block: Any = "latest") -> list[dict[str, Any]]:
        self.calls.append((from_block, to_block))
        return [entry for entry in self.entries if entry["blockNumber"] >= from_block]


def created_event(task_id: bytes, block: int) -> dict[str, Any]:
    args = {
        "taskId": task_id,
        "creator": "0xabc",
        "descriptionHash": b"\x01" * 32,
        "deadline": int(time.time()) + 3600,
        "bountyAmount": 1,
    }
    return {"blockNumber": block, "args": args}


def funded_event(task_id: bytes, block: int) -> dict[str, Any]:
    return {"blockNumber": block, "args": {"taskId": task_id, "funder": "0xabc", "amount": 1}}


def accepted_event(task_id: bytes, block: int, worker: str) -> dict[str, Any]:
    return {"blockNumber": block, "args": {"taskId": task_id, "worker": worker}}


def chain_listener(
    redis: fakeredis.FakeRedis,
    genesis: str,
    head: int,
    created: list[dict[str, Any]] | None = None,
    funded: list[dict[str, Any]] | None = None,
    dispatch: Any = None,
    accepted: list[dict[str, Any]] | None = None,
    trigger: str = "task_created_funded",
) -> EventListener:
    return EventListener(
        web3=FakeChain(genesis, head),
        task_manager=SimpleNamespace(
            events=SimpleNamespace(TaskCreated=FakeEvents(created), BidAccepted=FakeEvents(accepted))
        ),
        escrow=SimpleNamespace(events=SimpleNamespace(TaskFunded=FakeEvents(funded))),
        redis_client=redis,
        dispatch=dispatch,
        trigger=trigger,
    )


def make_listener(redis: fakeredis.FakeRedis | None = None) -> EventListener:
    return EventListener(
        web3=FakeChain(GENESIS_A, head=100),
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


@pytest.mark.asyncio
async def test_listener_without_a_dispatch_target_never_claims_a_dispatch() -> None:
    redis = fakeredis.FakeRedis(decode_responses=False)
    listener = EventListener(SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), redis)
    await ready_task(listener, "monitor-only")

    assert await listener._try_dispatch("monitor-only") is False
    state = json.loads(await redis.get(f"{listener.TASK_STATE_PREFIX}monitor-only"))
    assert "dispatched" not in state
    assert "attempted" not in state


@pytest.mark.asyncio
async def test_failed_dispatch_is_retried_on_the_next_pass() -> None:
    redis = fakeredis.FakeRedis(decode_responses=False)
    calls: list[str] = []

    async def flaky(task: dict[str, Any]) -> None:
        calls.append(task["created"]["task_id"])
        if len(calls) == 1:
            raise RuntimeError("pipeline queue unavailable")

    listener = EventListener(SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), redis, dispatch=flaky)
    await ready_task(listener, "retry-me")

    assert await listener._try_dispatch("retry-me") is False
    assert await listener._try_dispatch("retry-me") is True
    assert calls == ["retry-me", "retry-me"]
    state = json.loads(await redis.get(f"{listener.TASK_STATE_PREFIX}retry-me"))
    assert state["dispatched"] is True


@pytest.mark.asyncio
async def test_task_interrupted_mid_dispatch_is_not_dispatched_again_after_restart() -> None:
    redis = fakeredis.FakeRedis(decode_responses=False)
    entered = asyncio.Event()

    async def hangs(task: dict[str, Any]) -> None:
        entered.set()
        await asyncio.Event().wait()

    first = EventListener(SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), redis, dispatch=hangs)
    await ready_task(first, "interrupted")
    attempt = asyncio.create_task(first._try_dispatch("interrupted"))
    await entered.wait()
    attempt.cancel()  # the process dies while the target is still working on the task
    with pytest.raises(asyncio.CancelledError):
        await attempt

    calls: list[dict[str, Any]] = []

    async def record(task: dict[str, Any]) -> None:
        calls.append(task)

    restarted = EventListener(SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), redis, dispatch=record)
    await restarted._restore_task_states()

    assert await restarted._try_dispatch("interrupted") is False
    assert calls == []


async def stored_progress(redis: fakeredis.FakeRedis, genesis: str, cursor: int) -> None:
    await redis.set(EventListener.CHAIN_KEY, genesis)
    await redis.set(EventListener.LAST_BLOCK_KEY, str(cursor))


@pytest.mark.asyncio
async def test_progress_for_the_same_chain_is_kept() -> None:
    redis = fakeredis.FakeRedis(decode_responses=False)
    await stored_progress(redis, GENESIS_A, 42)
    listener = chain_listener(redis, GENESIS_A, head=100)
    await ready_task(listener, "kept")

    await listener._align_with_chain()

    assert await listener._get_last_processed_block() == 42
    assert await redis.exists(f"{listener.TASK_STATE_PREFIX}kept") == 1


@pytest.mark.asyncio
async def test_progress_from_a_previous_chain_is_cleared() -> None:
    redis = fakeredis.FakeRedis(decode_responses=False)
    await stored_progress(redis, GENESIS_A, 10)
    old = chain_listener(redis, GENESIS_A, head=10)
    await ready_task(old, "from-old-chain")
    new = chain_listener(redis, GENESIS_B, head=7)
    await new._restore_task_states()

    await new._align_with_chain()

    assert await new._get_last_processed_block() == 0
    assert await redis.keys(f"{EventListener.TASK_STATE_PREFIX}*") == []
    assert new._task_events == {}
    assert await redis.get(EventListener.CHAIN_KEY) == GENESIS_B.encode()


@pytest.mark.asyncio
async def test_progress_saved_before_chain_identity_is_kept_when_it_fits_the_chain() -> None:
    redis = fakeredis.FakeRedis(decode_responses=False)
    await redis.set(EventListener.LAST_BLOCK_KEY, "5")
    listener = chain_listener(redis, GENESIS_A, head=100)

    await listener._align_with_chain()

    assert await listener._get_last_processed_block() == 5
    assert await redis.get(EventListener.CHAIN_KEY) == GENESIS_A.encode()


@pytest.mark.asyncio
async def test_progress_saved_before_chain_identity_is_cleared_when_the_cursor_is_past_the_head() -> None:
    redis = fakeredis.FakeRedis(decode_responses=False)
    await redis.set(EventListener.LAST_BLOCK_KEY, "10")
    listener = chain_listener(redis, GENESIS_A, head=7)
    await ready_task(listener, "from-another-chain")

    await listener._align_with_chain()

    assert await listener._get_last_processed_block() == 0
    assert await redis.keys(f"{EventListener.TASK_STATE_PREFIX}*") == []


@pytest.mark.asyncio
async def test_task_on_a_reset_chain_below_the_old_cursor_is_dispatched_once() -> None:
    """Regression: after a reset, a new task below the old cursor was never seen."""
    redis = fakeredis.FakeRedis(decode_responses=False)
    await stored_progress(redis, GENESIS_A, 10)
    task_id = b"\x30" * 32
    dispatched: list[dict[str, Any]] = []

    async def record(task: dict[str, Any]) -> None:
        dispatched.append(task)

    listener = chain_listener(
        redis,
        GENESIS_B,
        head=7,
        created=[created_event(task_id, 5)],
        funded=[funded_event(task_id, 7)],
        dispatch=record,
    )

    # Same steps as one poll in start(): align with the chain, then read from the cursor.
    await listener._align_with_chain()
    await listener._process_events(await listener._get_last_processed_block() + 1)
    await listener._process_events(await listener._get_last_processed_block() + 1)

    assert [task["created"]["task_id"] for task in dispatched] == [task_id.hex()]
    state = json.loads(await redis.get(f"{listener.TASK_STATE_PREFIX}{task_id.hex()}"))
    assert state["dispatched"] is True


@pytest.mark.asyncio
async def test_events_are_read_with_get_logs_over_the_requested_range() -> None:
    redis = fakeredis.FakeRedis(decode_responses=False)
    listener = chain_listener(redis, GENESIS_A, head=100)

    await listener._process_events(3, "latest")

    assert listener.task_manager.events.TaskCreated.calls == [(3, "latest")]
    assert listener.escrow.events.TaskFunded.calls == [(3, "latest")]


TASK = b"\x30" * 32
WORKER = "0x" + "11" * 20


@pytest.mark.asyncio
async def test_the_default_trigger_dispatches_without_a_bid() -> None:
    redis = fakeredis.FakeRedis(decode_responses=False)
    dispatched: list[dict[str, Any]] = []
    listener = chain_listener(
        redis,
        GENESIS_A,
        head=100,
        created=[created_event(TASK, 5)],
        funded=[funded_event(TASK, 6)],
        dispatch=dispatched.append,
    )

    await listener._process_events(1)

    assert [task["created"]["task_id"] for task in dispatched] == [TASK.hex()]


@pytest.mark.asyncio
async def test_the_bid_accepted_trigger_waits_for_the_winning_bid() -> None:
    redis = fakeredis.FakeRedis(decode_responses=False)
    dispatched: list[dict[str, Any]] = []
    listener = chain_listener(
        redis,
        GENESIS_A,
        head=100,
        created=[created_event(TASK, 5)],
        funded=[funded_event(TASK, 6)],
        dispatch=dispatched.append,
        trigger="bid_accepted",
    )

    await listener._process_events(1)
    assert dispatched == []

    listener.task_manager.events.BidAccepted.entries.append(accepted_event(TASK, 7, WORKER))
    await listener._process_events(7)

    assert len(dispatched) == 1
    state = json.loads(await redis.get(f"{listener.TASK_STATE_PREFIX}{TASK.hex()}"))
    assert state["dispatched"] is True
    assert state["accepted"]["worker"] == WORKER


@pytest.mark.asyncio
async def test_a_bid_accepted_task_is_dispatched_once_even_after_a_restart() -> None:
    redis = fakeredis.FakeRedis(decode_responses=False)
    events = {"created": [created_event(TASK, 5)], "funded": [funded_event(TASK, 6)], "accepted": [accepted_event(TASK, 7, WORKER)]}
    first_dispatches: list[dict[str, Any]] = []
    first = chain_listener(redis, GENESIS_A, head=100, created=events["created"], funded=events["funded"], accepted=events["accepted"], dispatch=first_dispatches.append, trigger="bid_accepted")
    await first._process_events(1)

    restart_dispatches: list[dict[str, Any]] = []
    restarted = chain_listener(redis, GENESIS_A, head=100, created=events["created"], funded=events["funded"], accepted=events["accepted"], dispatch=restart_dispatches.append, trigger="bid_accepted")
    await restarted._process_events(1)

    assert len(first_dispatches) == 1
    assert restart_dispatches == []


def test_an_unknown_execution_trigger_is_rejected() -> None:
    redis = fakeredis.FakeRedis(decode_responses=False)
    with pytest.raises(ValueError, match="unknown execution trigger"):
        chain_listener(redis, GENESIS_A, head=1, trigger="on_tuesdays")

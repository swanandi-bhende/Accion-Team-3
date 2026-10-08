from types import SimpleNamespace

import fakeredis.aioredis as fakeredis
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from event_listener.listener import EventListener


class FakeFilter:
    def __init__(self, events):
        self.events = events

    def get_all_entries(self):
        return list(self.events)


class FakeEventFactory:
    def __init__(self, events):
        self.events = events

    def create_filter(self, **kwargs):
        return FakeFilter(self.events)


class FakeContract:
    def __init__(self, created=None, funded=None):
        self.events = SimpleNamespace(
            TaskCreated=FakeEventFactory(created or []),
            TaskFunded=FakeEventFactory(funded or []),
        )


def event(args, block_number):
    return {"args": args, "blockNumber": block_number}


def listener(created=None, funded=None, now=1000.0, dispatch=None, redis=None):
    return EventListener(
        web3=SimpleNamespace(),
        task_manager=FakeContract(created=created),
        escrow=FakeContract(funded=funded),
        redis_client=redis or fakeredis.FakeRedis(decode_responses=False),
        dispatch=dispatch or (lambda task: None),
        clock=lambda: now,
    )


@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    description_hash=st.binary(min_size=1, max_size=32),
    deadline=st.integers(min_value=1001, max_value=100000),
    bounty=st.integers(min_value=0, max_value=10**18),
)
@pytest.mark.asyncio
async def test_property_12_created_fields_are_extracted_exactly(description_hash, deadline, bounty):
    task_id = b"task-12"
    creator = "0x0000000000000000000000000000000000000001"
    created = event(
        {
            "taskId": task_id,
            "creator": creator,
            "descriptionHash": description_hash,
            "deadline": deadline,
            "bountyAmount": bounty,
        },
        12,
    )
    listener_instance = listener(created=[created], now=1000.0)

    await listener_instance._process_events(1, 12)

    stored = listener_instance._task_events[task_id.hex()]["created"]
    assert stored["task_id"] == task_id.hex()
    assert stored["creator"] == creator
    assert stored["description_hash"] == description_hash
    assert stored["deadline"] == deadline
    assert stored["bounty_amount"] == bounty


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("has_created", "has_funded", "should_dispatch"),
    [(False, False, False), (True, False, False), (False, True, False), (True, True, True)],
)
async def test_property_13_dispatch_requires_both_events(has_created, has_funded, should_dispatch):
    task_id = b"task-13"
    created = event(
        {
            "taskId": task_id,
            "creator": "creator",
            "descriptionHash": b"description",
            "deadline": 5000,
            "bountyAmount": 1,
        },
        1,
    )
    funded = event({"taskId": task_id, "funder": "funder", "fundedAmount": 1}, 2)
    calls = []
    listener_instance = listener(
        created=[created] if has_created else [],
        funded=[funded] if has_funded else [],
        dispatch=lambda task: calls.append(task),
    )

    await listener_instance._process_events(1, 2)

    assert bool(calls) is should_dispatch


@pytest.mark.asyncio
async def test_property_13_dispatch_is_idempotent():
    task_id = b"task-idempotent"
    created = event({"taskId": task_id, "creator": "c", "descriptionHash": b"d", "deadline": 5000, "bountyAmount": 1}, 1)
    funded = event({"taskId": task_id, "funder": "f", "fundedAmount": 1}, 2)
    calls = []
    listener_instance = listener(created=[created], funded=[funded], dispatch=lambda task: calls.append(task))

    await listener_instance._process_events(1, 2)
    await listener_instance._try_dispatch(task_id.hex())

    assert len(calls) == 1
    assert listener_instance._task_events[task_id.hex()]["dispatched"] is True


@pytest.mark.asyncio
async def test_property_14_out_of_order_events_dispatch_once_after_restart():
    task_id = b"task-restart"
    created = event({"taskId": task_id, "creator": "c", "descriptionHash": b"d", "deadline": 5000, "bountyAmount": 1}, 2)
    funded = event({"taskId": task_id, "funder": "f", "fundedAmount": 1}, 1)
    calls = []
    redis = fakeredis.FakeRedis(decode_responses=False)
    first = listener(funded=[funded], dispatch=lambda task: calls.append(task), redis=redis)
    await first._process_events(1, 1)
    second = listener(created=[created], funded=[funded], dispatch=lambda task: calls.append(task), redis=redis)
    await second._process_events(2, 2)
    await second._process_events(1, 2)

    assert len(calls) == 1
    assert await second._get_last_processed_block() == 2


@pytest.mark.asyncio
async def test_property_14_duplicate_event_at_new_block_does_not_dispatch_twice():
    task_id = b"task-reorg"
    created = event({"taskId": task_id, "creator": "c", "descriptionHash": b"d", "deadline": 5000, "bountyAmount": 1}, 10)
    funded = event({"taskId": task_id, "funder": "f", "fundedAmount": 1}, 11)
    reorg_created = event({"taskId": task_id, "creator": "c", "descriptionHash": b"d", "deadline": 5000, "bountyAmount": 1}, 12)
    reorg_funded = event({"taskId": task_id, "funder": "f", "fundedAmount": 1}, 13)
    calls = []
    redis = fakeredis.FakeRedis(decode_responses=False)
    instance = listener(created=[created], funded=[funded], dispatch=lambda task: calls.append(task), redis=redis)

    await instance._process_events(10, 11)
    instance.task_manager = FakeContract(created=[reorg_created])
    instance.escrow = FakeContract(funded=[reorg_funded])
    await instance._process_events(12, 13)

    assert len(calls) == 1
    assert instance._task_events[task_id.hex()]["dispatched"] is True


@pytest.mark.asyncio
async def test_expired_created_event_never_enters_pending_state():
    task_id = b"expired"
    created = event({"taskId": task_id, "creator": "c", "descriptionHash": b"d", "deadline": 1000, "bountyAmount": 1}, 3)
    listener_instance = listener(created=[created], now=1000.0)

    await listener_instance._process_events(3, 3)

    assert task_id.hex() not in listener_instance._task_events


@pytest.mark.asyncio
async def test_property_15_reconnect_backoff_caps_at_60_seconds():
    delays = []
    attempts = 0

    async def connect():
        nonlocal attempts
        attempts += 1
        if attempts <= 8:
            raise ConnectionError("dropped")
        return "connected"

    async def record_sleep(delay):
        delays.append(delay)

    listener_instance = listener()
    listener_instance.sleep = record_sleep

    assert await listener_instance._reconnect_with_backoff(connect) == "connected"
    assert delays == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]


@pytest.mark.asyncio
async def test_burst_of_tasks_dispatches_all_tasks():
    created = []
    funded = []
    for index in range(20):
        task_id = f"task-{index}".encode()
        created.append(event({"taskId": task_id, "creator": "c", "descriptionHash": b"d", "deadline": 5000, "bountyAmount": index}, 10))
        funded.append(event({"taskId": task_id, "funder": "f", "fundedAmount": index}, 10))
    calls = []
    listener_instance = listener(created=created, funded=funded, dispatch=lambda task: calls.append(task))

    await listener_instance._process_events(10, 10)

    assert len(calls) == 20


@pytest.mark.asyncio
async def test_abandoned_pending_tasks_are_cleaned_up_after_ttl():
    current_time = [4000.0]
    listener_instance = EventListener(
        web3=SimpleNamespace(),
        task_manager=FakeContract(),
        escrow=FakeContract(),
        redis_client=fakeredis.FakeRedis(decode_responses=False),
        clock=lambda: current_time[0],
    )
    listener_instance._task_events["abandoned"] = {"funded": {"funded_amount": 1}}
    listener_instance._pending_since["abandoned"] = 0.0

    removed = await listener_instance.cleanup_pending()

    assert removed == 1
    assert "abandoned" not in listener_instance._task_events

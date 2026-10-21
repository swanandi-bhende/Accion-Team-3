"""The listener consuming real TaskCreated / TaskFunded logs from Team A's contracts.

Uses the shared fixtures in tests/conftest.py, which install Team A's deployed
bytecode on the local node (tests/chain_helper.py).
"""

from typing import Any

import fakeredis.aioredis as fakeredis
import pytest
from web3 import Web3

from event_listener.listener import EventListener
from tests import chain_helper


@pytest.mark.asyncio
async def test_listener_processes_real_team_a_chain_events(team_a_chain: tuple[Web3, Any, Any]) -> None:
    web3, task_manager, escrow = team_a_chain
    token = chain_helper.token_contract(web3)

    task_id, _created, _funded = chain_helper.create_and_fund(web3, task_manager, escrow, token)

    dispatches: list[Any] = []
    listener = EventListener(
        web3=web3,
        task_manager=task_manager,
        escrow=escrow,
        redis_client=fakeredis.FakeRedis(decode_responses=False),
        dispatch=lambda task: dispatches.append(task),
    )

    # Scan only this task's block range; the node is shared with other tests.
    await listener._process_events(_created["blockNumber"], _funded["blockNumber"])

    assert task_id.hex() in listener._task_events
    assert len(dispatches) == 1
    assert dispatches[0]["created"]["task_id"] == task_id.hex()
    assert dispatches[0]["funded"]["funded_amount"] == web3.to_wei(1, "ether")

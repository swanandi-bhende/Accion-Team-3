"""Shared test fixtures.

One Hardhat node per session (so node startup is paid once and teardown cannot
race another module's) running **Team A's deployed contracts**: their bytecode is
replayed onto the node by ``scripts/ops/install_team_a_local`` and wired the same
way they are wired on Galileo, so these tests exercise the real authorisation and
lifecycle rules.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

import pytest
from web3 import Web3
from web3.contract import Contract
from web3.types import TxReceipt

from common.interfaces import StorageBackend
from tests import chain_helper

ROOT = chain_helper.ROOT
RPC_URL = chain_helper.RPC_URL


class InMemoryStorage(StorageBackend):
    """Content-addressed storage double, so tests need no IPFS node.

    ``tamper()`` makes every later ``retrieve`` return bytes that differ from
    what was uploaded, which is how the integrity-check failure is injected
    after a model has registered successfully.
    """

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.corrupt = False

    async def upload(self, data: bytes) -> str:
        cid = "Qm" + hashlib.sha256(data).hexdigest()
        self.store[cid] = data
        return cid

    async def retrieve(self, cid: str) -> bytes:
        if self.corrupt:
            return b"tampered-bytes-that-do-not-match-the-committed-hash"
        return self.store[cid]

    def tamper(self) -> None:
        self.corrupt = True


@pytest.fixture
def memory_storage() -> InMemoryStorage:
    return InMemoryStorage()


@pytest.fixture(scope="session")
def hardhat_node() -> Any:
    """A local Hardhat node, reusing one already listening on 8545."""
    yield from chain_helper.connect_or_start_node()


@pytest.fixture(scope="module")
def team_a_chain(hardhat_node: Web3) -> tuple[Web3, Contract, Contract]:
    """``(web3, task_manager, escrow)``: Team A's contracts, installed and wired."""
    deployment = chain_helper.install_team_a(hardhat_node)
    return hardhat_node, deployment["task_manager"], deployment["escrow"]


@pytest.fixture
def create_funded_task(team_a_chain: tuple[Web3, Contract, Contract]) -> Callable[..., tuple[Any, TxReceipt, TxReceipt]]:
    """Callable that posts ``TaskCreated`` then funds it through the ERC-20 escrow."""
    web3, task_manager, escrow = team_a_chain
    token = chain_helper.token_contract(web3)

    def create_and_fund(description_hash: bytes, bounty_ether: int = 1) -> tuple[Any, TxReceipt, TxReceipt]:
        task_id, created, funded = chain_helper.create_and_fund(
            web3, task_manager, escrow, token, description_hash, web3.to_wei(bounty_ether, "ether")
        )
        return task_id, created, funded

    return create_and_fund

"""The Team A preflight, run against Team A's contracts on the local node.

The same code path is what Step 18.5 will run against Galileo once Team A publish an
RPC endpoint; running it here proves the checks themselves work, not just that a
script exists.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from web3 import Web3

from scripts.ops import team_a
from scripts.ops.team_a_preflight import preflight, render
from tests import chain_helper

# Hardhat's well-known account #0 key; public test data, never used on a network.
ACCOUNT_ZERO_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"


@pytest.fixture(scope="module")
def chain() -> Iterator[Web3]:
    nodes = chain_helper.connect_or_start_node()
    web3 = next(nodes)
    try:
        chain_helper.install_team_a(web3)
        yield web3
    finally:
        nodes.close()


def status(checks: list[Any], name: str) -> str:
    return str(next(check.status for check in checks if check.name == name))


def test_every_check_passes_against_team_as_contracts(chain: Web3) -> None:
    checks = preflight(chain, expected_chain_id=16602, private_key=ACCOUNT_ZERO_KEY)

    failed = [check for check in checks if check.status == "failed"]
    assert failed == [], [render([check]) for check in failed]
    assert status(checks, "chain_id") == "passed"
    assert status(checks, "code:TaskManager") == "passed"
    assert status(checks, "abi") == "passed"
    assert status(checks, "prover") == "passed"


def test_wrong_chain_is_reported_before_anything_else(chain: Web3) -> None:
    checks = preflight(chain, expected_chain_id=31337)

    assert status(checks, "chain_id") == "failed"
    assert "31337" in next(check.detail for check in checks if check.name == "chain_id")


def test_an_address_without_code_fails_the_code_check(chain: Web3) -> None:
    addresses = dict(team_a.load_addresses())
    addresses["SETTLEMENT_ADDRESS"] = "0x" + "11" * 20

    checks = preflight(chain, expected_chain_id=16602, private_key=ACCOUNT_ZERO_KEY, addresses=addresses)

    assert status(checks, "code:SettlementContract") == "failed"


def test_unlisted_signer_fails_the_prover_check(chain: Web3) -> None:
    task_manager = team_a.contract(chain, "TaskManager")
    owner = chain.eth.accounts[0]
    task_manager.functions.setProver(owner, False).transact({"from": owner})
    try:
        checks = preflight(chain, expected_chain_id=16602, private_key=ACCOUNT_ZERO_KEY)
    finally:
        task_manager.functions.setProver(owner, True).transact({"from": owner})

    assert status(checks, "prover") == "failed"
    detail = next(check.detail for check in checks if check.name == "prover")
    assert "setProver" in detail


def test_absent_signer_is_skipped_not_failed(chain: Web3) -> None:
    # A node with unlocked accounts (local only) resolves a signer; asking for an
    # explicit empty key still falls back to account[0], so the skip path is the
    # one a node with no accounts and no key would take.
    checks = preflight(chain, expected_chain_id=16602, private_key="")

    assert status(checks, "signer") == "passed"
    assert status(checks, "prover") == "passed"

"""Handles for Team A's committed handoff: addresses, ABIs and contract objects.

Team A ship bare ABI exports plus a deployment record, not Solidity, so every
script that talks to their contracts loads the same two files. Keeping that in one
place means a re-deploy is a change to ``deployments/galileo.json`` and nothing
else.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from web3 import Web3

ROOT = Path(__file__).resolve().parents[2]
DEPLOYMENTS = ROOT / "deployments" / "galileo.json"
ABIS = ROOT / "team_a_assets"
DEFAULT_RPC_URL = "http://127.0.0.1:8545"

ADDRESS_KEYS = {
    "TaskManager": "TASK_MANAGER_ADDRESS",
    "EscrowContract": "ESCROW_ADDRESS",
    "SettlementContract": "SETTLEMENT_ADDRESS",
    "StakingContract": "STAKING_ADDRESS",
    "IdentityContract": "IDENTITY_ADDRESS",
}

# The TEST token's ABI was not part of the handoff; only the ERC-20 surface the
# scripts need is spelled out here.
TOKEN_ABI: list[dict[str, Any]] = json.loads(
    '[{"type":"function","name":"approve","stateMutability":"nonpayable",'
    '"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],'
    '"outputs":[{"name":"","type":"bool"}]},'
    '{"type":"function","name":"balanceOf","stateMutability":"view",'
    '"inputs":[{"name":"account","type":"address"}],"outputs":[{"name":"","type":"uint256"}]}]'
)


def load_addresses() -> dict[str, str]:
    return cast(dict[str, str], json.loads(DEPLOYMENTS.read_text(encoding="utf-8")))


def load_abi(name: str) -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], json.loads((ABIS / f"{name}.abi.json").read_text(encoding="utf-8")))


def contract(web3: Web3, name: str, addresses: dict[str, str] | None = None) -> Any:
    record = addresses or load_addresses()
    address = record[ADDRESS_KEYS[name]]
    return web3.eth.contract(address=web3.to_checksum_address(address), abi=load_abi(name))


def token_contract(web3: Web3, addresses: dict[str, str] | None = None) -> Any:
    record = addresses or load_addresses()
    address = record.get("BOUNTY_TOKEN_ADDRESS") or record["TEST_TOKEN_ADDRESS"]
    return web3.eth.contract(address=web3.to_checksum_address(address), abi=TOKEN_ABI)


def hex_to_bytes32(value: str) -> bytes:
    """A ``bytes32`` from a 32-byte hex digest, as the contracts expect."""
    text = value.removeprefix("0x")
    if len(text) != 64:
        raise ValueError(f"expected 32 bytes (64 hex characters), got {len(text)}: {value!r}")
    return bytes.fromhex(text)

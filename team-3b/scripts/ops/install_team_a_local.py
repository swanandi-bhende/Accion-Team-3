"""Install Team A's deployed contracts onto a local Hardhat node.

Team A hand over ABIs and deployed addresses, not Solidity source, so their
contracts cannot be compiled locally. Their runtime bytecode can be *replayed*
exactly though: `hardhat_setCode` places the same bytes that run on 0G Galileo at
the same addresses on this node, and the constructor state that lives in storage
is reproduced here.

What the constructor would have written, and how this script restores it:

* ``TaskManager.owner`` / ``EscrowContract.owner`` — storage slot 0, seeded with
  the local deployer so the owner-gated calls (``setProver``,
  ``setTaskManagerContract``) work as they did at Team A's deployment.
* ``EscrowContract.token`` — an **immutable**, so it is already baked into the
  bytecode and points at the Galileo TEST token address; the ERC-20 is installed
  there too, and the local funder's balance is seeded in its balances mapping
  (base slot 0, probed rather than assumed).
* ``EscrowContract.taskManager`` / ``settlementContract`` — set through
  ``setTaskManagerContract`` / ``setSettlementContract`` as the owner, the same
  calls Team A made on Galileo.
* ``TaskManager.isProver`` — the local signer is allow-listed, mirroring the
  ``setProver`` call Team A still owes us for the real network.

Usage::

    python -m scripts.ops.install_team_a_local

Reads ``WEB3_RPC_URL`` (default http://127.0.0.1:8545) and is idempotent: a node
that already carries the contracts is left untouched, so compose can rerun it.
"""

import json
import os
from pathlib import Path
from typing import Any, cast

from eth_abi import encode as abi_encode
from eth_utils import keccak
from web3 import Web3
from web3.types import RPCEndpoint

ROOT = Path(__file__).resolve().parents[2]
BYTECODE_RECORD = ROOT / "team_a_assets" / "bytecode.json"
ABIS = ROOT / "team_a_assets"
DEFAULT_RPC = "http://127.0.0.1:8545"
SEED_SLOT_RANGE = range(0, 16)
FUNDER_BALANCE = 10**24

# A minimal ERC-20 view surface: the TEST token's ABI was not part of the handoff.
TOKEN_ABI = json.loads(
    '[{"type":"function","name":"balanceOf","stateMutability":"view",'
    '"inputs":[{"name":"account","type":"address"}],"outputs":[{"name":"","type":"uint256"}]}]'
)


def rpc(web3: Web3, method: str, params: list[Any]) -> Any:
    result = web3.provider.make_request(cast(RPCEndpoint, method), params)
    if "error" in result:
        raise RuntimeError(f"{method}: {result['error']}")
    return result.get("result")


def word(value: Any) -> str:
    """A 32-byte hex value, as hardhat_setStorageAt requires."""
    if isinstance(value, str) and value.startswith("0x"):
        return "0x" + value[2:].lower().rjust(64, "0")
    if isinstance(value, int):
        return "0x" + value.to_bytes(32, "big").hex()
    return "0x" + bytes(value).hex()


def contract(web3: Web3, name: str, address: str) -> Any:
    abi = json.loads((ABIS / f"{name}.abi.json").read_text(encoding="utf-8"))
    return web3.eth.contract(address=web3.to_checksum_address(address), abi=abi)


def find_slot(web3: Web3, address: str, target: Any, getter: Any, slots: Any = SEED_SLOT_RANGE) -> int | None:
    """Find the storage slot holding ``getter``'s value, by trial.

    Returns ``None`` when the value is already correct without seeding (an
    immutable baked into the bytecode reads back its deployed value).
    """
    current = getter.call()
    if current == target:
        return None
    for slot in slots:
        rpc(web3, "hardhat_setStorageAt", [address, hex(slot), word(0)])
        rpc(web3, "hardhat_setStorageAt", [address, hex(slot), word(target)])
        if getter.call() == target:
            return int(slot)
        rpc(web3, "hardhat_setStorageAt", [address, hex(slot), word(0)])
    raise SystemExit(f"could not locate the storage slot behind {getter}")


def seed_balance(web3: Web3, token_address: str, account: str, amount: int) -> int:
    """Fund ``account`` in the installed TEST token, locating the balances mapping."""
    token = web3.eth.contract(address=web3.to_checksum_address(token_address), abi=TOKEN_ABI)
    for base in SEED_SLOT_RANGE:
        key = "0x" + keccak(abi_encode(["address", "uint256"], [account, base])).hex()
        rpc(web3, "hardhat_setStorageAt", [token_address, key, word(amount)])
        if token.functions.balanceOf(account).call() == amount:
            return base
        rpc(web3, "hardhat_setStorageAt", [token_address, key, word(0)])
    raise SystemExit("could not locate the TEST token balances mapping")


def install(web3: Web3, funder: str | None = None) -> dict[str, str]:
    record = json.loads(BYTECODE_RECORD.read_text(encoding="utf-8"))
    if int(web3.eth.chain_id) != record["chain_id"]:
        raise SystemExit(
            f"node reports chain id {web3.eth.chain_id}, the bytecode record is for "
            f"{record['chain_id']}; run chain/ (its hardhat config sets 16602)"
        )

    contracts = record["contracts"]
    accounts = list(web3.eth.accounts)
    owner = accounts[0]
    funder = funder or (accounts[2] if len(accounts) > 2 else owner)

    fresh = False
    for _name, entry in contracts.items():
        address = web3.to_checksum_address(entry["address"])
        code = web3.eth.get_code(address).hex()
        if code != entry["runtime_bytecode"].removeprefix("0x"):
            rpc(web3, "hardhat_setCode", [address, entry["runtime_bytecode"]])
            fresh = True
    print(f"installed {len(contracts)} Team A contracts at their Galileo addresses")

    task_manager = contract(web3, "TaskManager", contracts["TaskManager"]["address"])
    escrow = contract(web3, "EscrowContract", contracts["EscrowContract"]["address"])
    token_address = web3.to_checksum_address(contracts["TEST_TOKEN"]["address"])
    settlement = contracts["SettlementContract"]["address"]

    owner_slot = find_slot(web3, task_manager.address, owner, task_manager.functions.owner())
    escrow_owner_slot = find_slot(web3, escrow.address, owner, escrow.functions.owner())
    escrow_token_slot = find_slot(web3, escrow.address, token_address, escrow.functions.token())
    balance_slot = seed_balance(web3, token_address, funder, FUNDER_BALANCE)
    for label, slot in (
        ("TaskManager.owner", owner_slot),
        ("EscrowContract.owner", escrow_owner_slot),
        ("EscrowContract.token", escrow_token_slot),
    ):
        # A None slot means the value already reads back correctly: either it was
        # seeded by an earlier install, or (the token) it is an immutable baked
        # into the bytecode.
        print(f"{label}: {'already correct' if slot is None else f'store at slot {slot}'}")
    print(f"TEST token balances mapping: base slot {balance_slot}; funded {funder}")

    def send(tx: Any, label: str) -> None:
        receipt = web3.eth.wait_for_transaction_receipt(tx)
        if receipt["status"] != 1:
            raise SystemExit(f"{label}: transaction reverted")

    # Wire the contracts to each other, as Team A did on Galileo.
    if escrow.functions.taskManager().call() != task_manager.address:
        send(escrow.functions.setTaskManagerContract(task_manager.address).transact({"from": owner}),
             "escrow.setTaskManagerContract")
    if escrow.functions.settlementContract().call() != web3.to_checksum_address(settlement):
        send(escrow.functions.setSettlementContract(settlement).transact({"from": owner}),
             "escrow.setSettlementContract")
    if task_manager.functions.settlementContract().call() != web3.to_checksum_address(settlement):
        send(task_manager.functions.setSettlementContract(settlement).transact({"from": owner}),
             "taskManager.setSettlementContract")

    # Mirror the setProver call Team A owes us on the real network.
    if not task_manager.functions.isProver(owner).call():
        send(task_manager.functions.setProver(owner, True).transact({"from": owner}), "taskManager.setProver")

    summary: dict[str, str] = {
        name: str(web3.to_checksum_address(entry["address"])) for name, entry in contracts.items()
    }
    print(f"TaskManager.owner={task_manager.functions.owner().call()}")
    print(f"EscrowContract.token={escrow.functions.token().call()}")
    print(f"TaskManager.isProver(owner)={task_manager.functions.isProver(owner).call()}")
    print(f"freshly installed: {fresh or 'already present'}")
    print(f"funder={funder}")
    return summary


def main() -> None:
    rpc_url = os.environ.get("WEB3_RPC_URL") or DEFAULT_RPC
    web3 = Web3(Web3.HTTPProvider(rpc_url))
    if not web3.is_connected():
        raise SystemExit(f"no chain at {rpc_url}; start the node first (docker compose up -d hardhat-node)")
    print(f"installing Team A contracts on {rpc_url} (chain id {web3.eth.chain_id})")
    install(web3)


if __name__ == "__main__":
    main()

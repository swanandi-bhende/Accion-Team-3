"""Run one task through Team A's contracts on Galileo, then wait for its payout.

This drives the on-chain sequence: createTask as the creator, placeBid as the worker,
approve and deposit TEST, then acceptBid. The proof path runs in our services (event
listener, pipeline, and a proof submitter with SETTLE_AFTER_PROOF=true), which settle the
task. This script then waits for the TaskSettled event.

Without --execute it only runs a read-only preflight and sends nothing.

Keys come from the environment and are never printed:
  RUNBOOK_CREATOR_PRIVATE_KEY  pays gas and the TEST bounty
  RUNBOOK_WORKER_PRIVATE_KEY   the bidder (Team C's agent wallet in a shared run)
"""

import argparse
import os
import time
from typing import Any

from eth_account import Account
from web3 import Web3

from common.config import get_settings
from common.web3_checks import verify_chain_id
from event_listener.config import load_contract_abi

ERC20_ABI = [
    {"name": "approve", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "outputs": [{"name": "", "type": "bool"}]},
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "account", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}]},
]
ZERO_BYTES32 = "0x" + "00" * 32


def send(web3: Any, account: Any, call: Any) -> Any:
    """Sign and send one call as ``account``, wait for it, and stop if it reverts."""
    transaction = call.build_transaction(
        {"from": account.address, "nonce": web3.eth.get_transaction_count(account.address)}
    )
    signed = account.sign_transaction(transaction)
    receipt = web3.eth.wait_for_transaction_receipt(web3.eth.send_raw_transaction(signed.raw_transaction))
    if receipt["status"] != 1:
        raise SystemExit(f"transaction reverted: {call.fn_name} tx={receipt['transactionHash'].hex()}")
    print(f"  {call.fn_name}: tx={receipt['transactionHash'].hex()}")
    return receipt


def key_from_env(name: str) -> Any:
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"{name} is not set")
    return Account.from_key(value)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model-id", required=True, help="registered model the task asks for")
    parser.add_argument("--bounty", type=int, default=10**18, help="TEST bounty in wei (default 1 TEST)")
    parser.add_argument("--price", type=int, help="bid price in wei (default: the bounty)")
    parser.add_argument("--eta-s", type=int, default=300, help="bid ETA in seconds")
    parser.add_argument("--deadline-s", type=int, default=900, help="seconds until the task deadline")
    parser.add_argument("--capability-proof-hash", default=ZERO_BYTES32, help="bytes32 agreed with Team C")
    parser.add_argument("--timeout-s", type=int, default=1200, help="how long to wait for TaskSettled")
    parser.add_argument("--execute", action="store_true", help="send the transactions (default: preflight only)")
    args = parser.parse_args()

    settings = get_settings().require(
        "web3_rpc_url", "task_manager_address", "escrow_address", "settlement_address", "test_token_address"
    )
    web3 = Web3(Web3.HTTPProvider(settings.web3_rpc_url or ""))
    verify_chain_id(web3, settings.web3_chain_id, "galileo-runbook")
    creator = key_from_env("RUNBOOK_CREATOR_PRIVATE_KEY")
    worker = key_from_env("RUNBOOK_WORKER_PRIVATE_KEY")
    price = args.price if args.price is not None else args.bounty

    task_manager = web3.eth.contract(
        address=web3.to_checksum_address(settings.task_manager_address or ""),
        abi=load_contract_abi(settings.task_manager_artifact),
    )
    escrow = web3.eth.contract(
        address=web3.to_checksum_address(settings.escrow_address or ""),
        abi=load_contract_abi(settings.escrow_artifact),
    )
    settlement = web3.eth.contract(
        address=web3.to_checksum_address(settings.settlement_address or ""),
        abi=load_contract_abi("SettlementContract"),
    )
    token = web3.eth.contract(address=web3.to_checksum_address(settings.test_token_address or ""), abi=ERC20_ABI)

    checks = {
        "creator and worker are different accounts": creator.address != worker.address,
        "creator has gas": web3.eth.get_balance(creator.address) > 0,
        "worker has gas": web3.eth.get_balance(worker.address) > 0,
        f"creator holds the TEST bounty ({args.bounty} wei)": token.functions.balanceOf(creator.address).call()
        >= args.bounty,
        "price is within the bounty": 0 < price <= args.bounty,
        "settlement contract has code": len(web3.eth.get_code(settlement.address)) > 0,
    }
    print(f"Galileo runbook, chain {web3.eth.chain_id}, block {web3.eth.block_number}")
    print(f"  creator {creator.address}\n  worker  {worker.address}")
    for label, ok in checks.items():
        print(f"  [{'ok' if ok else 'FAIL'}] {label}")
    failed = [label for label, ok in checks.items() if not ok]
    if failed:
        raise SystemExit(f"preflight failed: {'; '.join(failed)}")
    if not args.execute:
        print("Preflight passed. Re-run with --execute to send the transactions.")
        return

    print("createTask")
    created = send(web3, creator, task_manager.functions.createTask(
        Web3.keccak(text=args.model_id), int(time.time()) + args.deadline_s, args.bounty
    ))
    task_id = task_manager.events.TaskCreated().process_receipt(created)[0]["args"]["taskId"]
    print(f"  taskId: 0x{task_id.hex()}")

    print("placeBid (worker)")
    send(web3, worker, task_manager.functions.placeBid(
        task_id, price, args.eta_s, Web3.to_bytes(hexstr=args.capability_proof_hash)
    ))

    print("approve and deposit TEST (creator)")
    send(web3, creator, token.functions.approve(escrow.address, args.bounty))
    send(web3, creator, escrow.functions.deposit(task_id, args.bounty))

    print("acceptBid (creator)")
    send(web3, creator, task_manager.functions.acceptBid(task_id, worker.address))
    accepted_worker = task_manager.functions.taskWorker(task_id).call()
    if accepted_worker != worker.address:
        raise SystemExit(f"taskWorker is {accepted_worker}, expected {worker.address}")
    print("  taskWorker is the bidder, so the job auth check will pass")

    print(f"waiting up to {args.timeout_s}s for TaskSettled (our services must be running)")
    deadline = time.time() + args.timeout_s
    while time.time() < deadline:
        logs = settlement.events.TaskSettled().get_logs(
            from_block=created["blockNumber"], argument_filters={"taskId": task_id}
        )
        if logs:
            args_out = logs[0]["args"]
            print(
                f"  TaskSettled: worker={args_out['worker']} workerAmount={args_out['workerAmount']} "
                f"stakerAmount={args_out['stakerAmount']} treasuryAmount={args_out['treasuryAmount']}"
            )
            return
        time.sleep(10)
    raise SystemExit(f"no TaskSettled for 0x{task_id.hex()} within {args.timeout_s}s")


if __name__ == "__main__":
    main()

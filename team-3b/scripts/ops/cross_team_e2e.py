"""Drive one task through all three teams, and say exactly how far it got (18.7).

The full three-team path is: Team C triggers a task -> Team A's contract emits
events -> our listener dispatches -> we execute and submit a proof -> Team A
settles -> Team C reads the result. Each leg depends on another team, so this
driver reports per leg and refuses to pretend: a missing endpoint exits 2 before
anything is created, and a leg that cannot run is ``skipped`` with the reason.

Local rehearsal (Team C's leg played by this script with the documented payload):

    python -m scripts.ops.cross_team_e2e \\
        --rpc-url http://127.0.0.1:8545 \\
        --inference-url http://127.0.0.1:8000 \\
        --redis-url redis://127.0.0.1:6379

Against Galileo, pass ``--private-key`` (a funded, allow-listed signer) and
``--worker`` (the address whose bid the creator accepts); both are required there
because a real node has no unlocked accounts.

Exit codes: 0 every leg passed, 1 a leg failed, 2 a required dependency is missing.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import redis
from eth_account import Account
from web3 import Web3

from scripts.ops import team_a
from scripts.ops.team_c_conformance import (
    Finding,
    post_payload,
    validate_payload,
    validate_receipt,
)

PASSED, FAILED, SKIPPED = "passed", "failed", "skipped"
DEFAULT_PAYLOAD: dict[str, Any] = {
    "model_id": "toy_linear",
    "input_data": base64.b64encode(b"hello").decode(),
}
DISPATCH_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class Leg:
    name: str
    status: str
    detail: str

    def render(self) -> str:
        return f"LEG={self.name} STATUS={self.status} {self.detail}"


def reference_payload(model_id: str, task_id: str) -> dict[str, Any]:
    payload = dict(DEFAULT_PAYLOAD)
    payload["model_id"] = model_id
    payload["task_id"] = task_id
    return payload


def receipt_findings(receipt: dict[str, Any], payload: dict[str, Any]) -> list[Finding]:
    findings = validate_payload(payload)
    findings.extend(validate_receipt(receipt, expected_model_id=str(payload["model_id"])))
    return findings


def _send(web3: Web3, call: Any, sender: str, label: str) -> Any:
    receipt = web3.eth.wait_for_transaction_receipt(call.transact({"from": sender}))
    if receipt["status"] != 1:
        raise RuntimeError(f"{label} reverted")
    return receipt


def _redis_dispatched(redis_url: str, task_id_hex: str, timeout_s: float) -> Leg:
    client = redis.Redis.from_url(redis_url, decode_responses=True)
    key = f"event_listener:task:{task_id_hex.removeprefix('0x')}"
    deadline = time.monotonic() + timeout_s
    try:
        while time.monotonic() < deadline:
            raw = client.get(key)
            if raw and json.loads(raw).get("dispatched") is True:
                return Leg("listener.dispatch", PASSED, f"{key} dispatched=true")
            time.sleep(1.0)
    finally:
        client.close()
    return Leg("listener.dispatch", FAILED, f"{key} never reported dispatched within {timeout_s:.0f}s")


def run(  # noqa: PLR0913 - a driver, one parameter per external dependency
    web3: Web3,
    *,
    inference_url: str,
    payload: dict[str, Any],
    private_key: str = "",
    worker: str | None = None,
    redis_url: str = "",
    bounty: int = 10**18,
    timeout_s: float = 120.0,
) -> list[Leg]:
    """Create, bid, prove and settle one task; returns every leg that was attempted."""
    addresses = team_a.load_addresses()
    task_manager = team_a.contract(web3, "TaskManager", addresses)
    escrow = team_a.contract(web3, "EscrowContract", addresses)
    settlement = team_a.contract(web3, "SettlementContract", addresses)
    token = team_a.token_contract(web3, addresses)

    accounts = list(web3.eth.accounts)
    if private_key:
        creator = str(Account.from_key(private_key).address)
        funder = creator
    else:
        creator = str(accounts[0])
        funder = str(accounts[2] if len(accounts) > 2 else accounts[0])
    worker = worker or (str(accounts[3]) if len(accounts) > 3 else creator)

    legs: list[Leg] = []

    description_hash = web3.keccak(text=str(payload["model_id"]))
    created = _send(
        web3,
        task_manager.functions.createTask(description_hash, int(time.time()) + 3600, bounty),
        creator,
        "createTask",
    )
    task_id = task_manager.events.TaskCreated().process_receipt(created)[0]["args"]["taskId"]
    task_id_hex = "0x" + str(task_id.hex())
    legs.append(Leg("team_a.task_created", PASSED, task_id_hex))

    _send(web3, token.functions.approve(escrow.address, bounty), funder, "approve")
    _send(web3, escrow.functions.deposit(task_id, bounty), funder, "deposit")
    legs.append(Leg("team_a.task_funded", PASSED, f"{bounty} from {funder}"))

    _send(
        web3,
        task_manager.functions.placeBid(task_id, bounty, 3600, web3.keccak(text="capability")),
        worker,
        "placeBid",
    )
    _send(web3, task_manager.functions.acceptBid(task_id, worker), creator, "acceptBid")
    legs.append(Leg("team_a.bid_accepted", PASSED, f"worker={worker}"))

    if redis_url:
        legs.append(_redis_dispatched(redis_url, task_id_hex, min(timeout_s, DISPATCH_TIMEOUT_S)))
    else:
        legs.append(Leg("listener.dispatch", SKIPPED, "no --redis-url"))

    request = dict(payload)
    request["task_id"] = task_id_hex
    status, receipt = post_payload(inference_url, request, timeout_s=timeout_s)
    if status != 200:
        legs.append(Leg("team_c.inference", FAILED, f"HTTP {status}: {json.dumps(receipt)[:300]}"))
        return legs

    findings = receipt_findings(receipt, request)
    errors = [finding for finding in findings if finding.severity == "error"]
    if errors:
        legs.append(
            Leg("team_c.inference", FAILED, "; ".join(f.detail for f in errors)[:400])
        )
        return legs
    legs.append(Leg("team_c.inference", PASSED, f"output_hash={receipt['output_hash']}"))

    proof_hash = web3.keccak(text=str(receipt["execution_id"]))
    result_hash = team_a.hex_to_bytes32(str(receipt["output_hash"]))
    proof_receipt = _send(
        web3,
        task_manager.functions.submitProofs(
            [task_id], [proof_hash], [str(receipt["model_cid"])], [result_hash]
        ),
        creator,
        "submitProofs",
    )
    proof_events = task_manager.events.ProofSubmitted().process_receipt(proof_receipt)
    submitted = proof_events[0]["args"] if proof_events else {}
    legs.append(
        Leg("three_b.proof", PASSED, f"tx={proof_receipt['transactionHash'].hex()} submitter={submitted.get('submitter')}")
    )

    _send(web3, settlement.functions.settleTask(task_id), creator, "settleTask")
    remaining = escrow.functions.getBalance(task_id).call()
    legs.append(
        Leg(
            "team_a.settled",
            PASSED if remaining == 0 else FAILED,
            f"status={task_manager.functions.getStatus(task_id).call()} escrow_remaining={remaining}",
        )
    )
    return legs


def _health(url: str, timeout_s: float = 5.0) -> bool:
    try:
        response = httpx.get(url.rstrip("/") + "/health", timeout=timeout_s)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rpc-url", default=os.environ.get("WEB3_RPC_URL") or team_a.DEFAULT_RPC_URL)
    parser.add_argument("--inference-url", default=os.environ.get("INFERENCE_URL", ""))
    parser.add_argument("--redis-url", default=os.environ.get("REDIS_URL", ""))
    parser.add_argument("--private-key", default=os.environ.get("PROOF_SUBMITTER_PRIVATE_KEY", ""))
    parser.add_argument("--worker", default="")
    parser.add_argument("--payload", type=Path, default=None, help="Team C's captured InferenceRequest")
    parser.add_argument("--model-id", default=str(DEFAULT_PAYLOAD["model_id"]))
    parser.add_argument("--bounty", type=int, default=10**18)
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args(argv)

    if not args.inference_url:
        print("LEG=setup STATUS=failed no --inference-url (Team C's side is missing)")
        return 2
    web3 = Web3(Web3.HTTPProvider(args.rpc_url, request_kwargs={"timeout": 20}))
    if not web3.is_connected():
        print(f"LEG=setup STATUS=failed no chain at {args.rpc_url} (Team A's side is missing)")
        return 2
    if not _health(args.inference_url):
        print(f"LEG=setup STATUS=failed {args.inference_url}/health is not answering")
        return 2

    if args.payload:
        payload = json.loads(args.payload.read_text(encoding="utf-8"))
    else:
        payload = reference_payload(args.model_id, "")

    legs = run(
        web3,
        inference_url=args.inference_url,
        payload=payload,
        private_key=args.private_key,
        worker=args.worker or None,
        redis_url=args.redis_url,
        bounty=args.bounty,
        timeout_s=args.timeout,
    )
    for leg in legs:
        print(leg.render())
    return 1 if any(leg.status == FAILED for leg in legs) else 0


if __name__ == "__main__":
    raise SystemExit(main())

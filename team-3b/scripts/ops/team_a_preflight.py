"""Read-only readiness check for a chain running Team A's contracts.

Step 18.5 asks for the on-chain suite to run against Team A's testnet. That needs
an endpoint and a funded signer, neither of which Team A has published yet, so this
script is the part that can run the moment they do — and the same check the local
replay passes, which is what makes the local result meaningful.

It never sends a transaction. It answers: is this endpoint Team A's chain, are
their contracts actually there, do the views decode with the committed ABIs, and is
our signer allowed to submit proofs?

Usage::

    python -m scripts.ops.team_a_preflight                     # local chain
    python -m scripts.ops.team_a_preflight --rpc-url "$WEB3_RPC_URL" \\
        --chain-id 16602 --private-key "$PROOF_SUBMITTER_PRIVATE_KEY"

Exit codes: 0 all checks passed, 1 a check failed, 2 the endpoint is unreachable.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass

from web3 import Web3

from common.web3_checks import prover_allowlist_status, signer_address
from scripts.ops import team_a

PASSED, FAILED, SKIPPED = "passed", "failed", "skipped"


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str

    @property
    def ok(self) -> bool:
        return self.status != FAILED


def preflight(
    web3: Web3,
    *,
    expected_chain_id: int | None = None,
    private_key: str = "",
    addresses: dict[str, str] | None = None,
) -> list[Check]:
    """Run every read-only check and return them in report order."""
    record = addresses or team_a.load_addresses()
    checks: list[Check] = []

    try:
        actual_chain_id = int(web3.eth.chain_id)
    except Exception as error:  # noqa: BLE001 - an unreachable RPC is the finding
        return [Check("rpc", FAILED, f"eth_chainId failed: {error}")]

    if expected_chain_id is None:
        checks.append(Check("chain_id", SKIPPED, f"{actual_chain_id} (no --chain-id given)"))
    elif actual_chain_id == expected_chain_id:
        checks.append(Check("chain_id", PASSED, str(actual_chain_id)))
    else:
        checks.append(
            Check(
                "chain_id",
                FAILED,
                f"endpoint reports {actual_chain_id}, expected Team A's {expected_chain_id}",
            )
        )

    for name in ("TaskManager", "EscrowContract", "SettlementContract"):
        address = record[team_a.ADDRESS_KEYS[name]]
        code = web3.eth.get_code(web3.to_checksum_address(address)).hex()
        present = code not in ("0x", "")
        checks.append(
            Check(f"code:{name}", PASSED if present else FAILED, f"{address} {len(code) // 2} bytes")
        )

    token_address = record.get("BOUNTY_TOKEN_ADDRESS") or record["TEST_TOKEN_ADDRESS"]
    token_code = web3.eth.get_code(web3.to_checksum_address(token_address)).hex()
    checks.append(
        Check(
            "code:TEST_TOKEN",
            PASSED if token_code not in ("0x", "") else FAILED,
            f"{token_address} {len(token_code) // 2} bytes",
        )
    )

    # Views decode only if the committed ABIs still match the deployed bytecode.
    try:
        task_manager = team_a.contract(web3, "TaskManager", record)
        escrow = team_a.contract(web3, "EscrowContract", record)
        settlement = team_a.contract(web3, "SettlementContract", record)
        owner = task_manager.functions.owner().call()
        max_batch = task_manager.functions.MAX_PROOF_BATCH().call()
        escrow_token = escrow.functions.token().call()
        escrow_task_manager = escrow.functions.taskManager().call()
        settlement_escrow = settlement.functions.escrow().call()
    except Exception as error:  # noqa: BLE001 - report, do not crash the preflight
        checks.append(Check("abi", FAILED, f"could not decode Team A's views: {error}"))
        return checks

    checks.append(Check("abi", PASSED, f"owner={owner} MAX_PROOF_BATCH={max_batch}"))
    checks.append(
        Check(
            "escrow.token",
            PASSED if escrow_token == web3.to_checksum_address(token_address) else FAILED,
            f"{escrow_token} (expected {token_address})",
        )
    )
    checks.append(
        Check(
            "escrow.taskManager",
            PASSED
            if escrow_task_manager == web3.to_checksum_address(record[team_a.ADDRESS_KEYS["TaskManager"]])
            else FAILED,
            escrow_task_manager,
        )
    )
    checks.append(
        Check(
            "settlement.escrow",
            PASSED
            if settlement_escrow == web3.to_checksum_address(record[team_a.ADDRESS_KEYS["EscrowContract"]])
            else FAILED,
            settlement_escrow,
        )
    )

    signer = signer_address(web3, private_key, "team-a-preflight")
    if signer is None:
        checks.append(Check("signer", SKIPPED, "no private key configured and the endpoint has no unlocked accounts"))
        checks.append(Check("prover", SKIPPED, "no signer to check"))
        return checks

    checks.append(Check("signer", PASSED, signer))
    allowed = prover_allowlist_status(task_manager, signer)
    if allowed is None:
        checks.append(Check("prover", SKIPPED, f"isProver({signer}) is not readable"))
    else:
        checks.append(
            Check(
                "prover",
                PASSED if allowed else FAILED,
                f"{signer} "
                + ("is an authorised prover" if allowed else "is NOT allow-listed; ask Team A to call setProver"),
            )
        )
    return checks


def render(checks: list[Check]) -> str:
    return "\n".join(f"CHECK={check.name} STATUS={check.status} {check.detail}" for check in checks)


def failed(checks: list[Check]) -> bool:
    return any(check.status == FAILED for check in checks)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rpc-url", default=os.environ.get("WEB3_RPC_URL") or team_a.DEFAULT_RPC_URL)
    parser.add_argument("--chain-id", type=int, default=None)
    parser.add_argument("--private-key", default=os.environ.get("PROOF_SUBMITTER_PRIVATE_KEY", ""))
    args = parser.parse_args(argv)

    web3 = Web3(Web3.HTTPProvider(args.rpc_url, request_kwargs={"timeout": 20}))
    if not web3.is_connected():
        print(f"CHECK=rpc STATUS=failed no endpoint at {args.rpc_url}")
        return 2

    checks = preflight(web3, expected_chain_id=args.chain_id, private_key=args.private_key)
    print(render(checks))
    return 1 if failed(checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())

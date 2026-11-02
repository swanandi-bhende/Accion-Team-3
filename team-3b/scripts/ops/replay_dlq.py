"""Inspect proof-submitter dead-letter entries and prepare manual replay."""

import argparse
import asyncio
import json

import redis.asyncio as redis
from web3 import Web3

from common.config import get_settings
from common.web3_checks import signer_address, verify_chain_id
from event_listener.config import load_contract_abi
from proof_submitter.submitter import ProofSubmitter, is_replayable


async def main() -> None:
    """Print the DLQ entry at ``--index``; ``--replay`` resubmits its replayable proofs.

    Only transient failures and ``InvalidPayload`` rejections are resubmitted. Proofs with
    a final rejection (TaskNotFound, InvalidStatus, ProofAlreadySubmitted) stay in the
    queue, so the entry keeps them for inspection.
    """
    parser =argparse.ArgumentParser()
    parser.add_argument("--index", type=int, default=0, help="DLQ index to inspect")
    parser.add_argument("--replay", action="store_true", help="Replay the selected entry")
    args = parser.parse_args()
    settings = get_settings().require("redis_url")
    client = redis.from_url(settings.redis_url, decode_responses=False)
    raw = await client.lindex(ProofSubmitter.DLQ_KEY, args.index)
    if raw is None:
        raise SystemExit("No DLQ entry at the requested index")
    entry = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
    print(json.dumps(entry, indent=2))
    if not args.replay:
        return

    replayable = [proof for proof in entry["proofs"] if is_replayable(proof)]
    final = [proof for proof in entry["proofs"] if not is_replayable(proof)]
    if not replayable:
        raise SystemExit("Nothing to replay: every proof in this entry has a final rejection")

    replay_settings = settings.require("web3_rpc_url", "proof_submitter_contract_address")
    web3 = Web3(Web3.HTTPProvider(replay_settings.web3_rpc_url or ""))
    verify_chain_id(web3, replay_settings.web3_chain_id, "replay-dlq")
    address = web3.to_checksum_address(replay_settings.proof_submitter_contract_address or "")
    contract = web3.eth.contract(
        address=address, abi=load_contract_abi(replay_settings.proof_submitter_artifact)
    )
    submitter = ProofSubmitter(
        web3,
        contract,
        client,
        replay_settings.proof_submitter_private_key,
        signer_address=signer_address(web3, replay_settings.proof_submitter_private_key, "replay-dlq"),
    )
    replay_proofs = [type("ReplayProof", (), proof)() for proof in replayable]
    # Submission dead-letters its own failures, so the original entry is removed either way.
    tx_hash = await submitter._submit_batch(replay_proofs)
    await client.lrem(ProofSubmitter.DLQ_KEY, 1, raw)
    if final:
        await client.lpush(ProofSubmitter.DLQ_KEY, json.dumps({**entry, "proofs": final}))
        print(f"Kept {len(final)} proof(s) with a final rejection in the DLQ")
    if submitter.metrics["dlq_batches"]:
        print("Replay failed again; the proofs were dead-lettered with the new error")
    elif tx_hash:
        print(f"Replayed: {tx_hash}")
    else:
        print("Nothing needed submitting: the tasks are closed or the proofs are on chain")


if __name__ == "__main__":
    asyncio.run(main())

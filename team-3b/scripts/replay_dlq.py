"""Inspect proof-submitter dead-letter entries and prepare manual replay."""

import argparse
import asyncio
import json
import os

import redis.asyncio as redis
from web3 import Web3

from event_listener.config import load_contract_abi
from proof_submitter.submitter import ProofSubmitter


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=int, default=0, help="DLQ index to inspect")
    parser.add_argument("--replay", action="store_true", help="Replay the selected entry")
    args = parser.parse_args()
    client = redis.from_url(os.environ["REDIS_URL"], decode_responses=False)
    raw = await client.lindex(ProofSubmitter.DLQ_KEY, args.index)
    if raw is None:
        raise SystemExit("No DLQ entry at the requested index")
    entry = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
    print(json.dumps(entry, indent=2))
    if not args.replay:
        return

    web3 = Web3(Web3.HTTPProvider(os.environ["WEB3_RPC_URL"]))
    address = web3.to_checksum_address(os.environ["PROOF_SUBMITTER_CONTRACT_ADDRESS"])
    contract = web3.eth.contract(address=address, abi=load_contract_abi("TaskManager"))
    submitter = ProofSubmitter(web3, contract, client, os.environ.get("PROOF_SUBMITTER_PRIVATE_KEY", ""))
    replay_proofs = [
        type("ReplayProof", (), proof)()
        for proof in entry["proofs"]
    ]
    tx_hash = await submitter._send_transaction(replay_proofs)
    await client.lrem(ProofSubmitter.DLQ_KEY, 1, raw)
    print(f"Replayed successfully: {tx_hash}")


if __name__ == "__main__":
    asyncio.run(main())

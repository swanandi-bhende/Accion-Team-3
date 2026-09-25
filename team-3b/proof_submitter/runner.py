import asyncio
import os

import redis.asyncio as redis
from web3 import Web3

from event_listener.config import load_contract_abi
from proof_submitter.submitter import ProofSubmitter


async def main() -> None:
    web3 = Web3(Web3.HTTPProvider(os.environ["WEB3_RPC_URL"]))
    address = web3.to_checksum_address(os.environ["PROOF_SUBMITTER_CONTRACT_ADDRESS"])
    contract = web3.eth.contract(address=address, abi=load_contract_abi("TaskManager"))
    redis_client = redis.from_url(os.environ["REDIS_URL"], decode_responses=False)
    ProofSubmitter(web3, contract, redis_client, os.environ.get("PROOF_SUBMITTER_PRIVATE_KEY", ""))
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
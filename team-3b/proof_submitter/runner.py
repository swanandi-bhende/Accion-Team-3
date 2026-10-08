import asyncio

import redis.asyncio as redis
from web3 import Web3

from common.config import get_settings
from event_listener.config import load_contract_abi
from proof_submitter.submitter import ProofSubmitter


async def main() -> None:
    settings = get_settings().require(
        "web3_rpc_url", "proof_submitter_contract_address", "redis_url"
    )
    web3 = Web3(Web3.HTTPProvider(settings.web3_rpc_url))
    address = web3.to_checksum_address(settings.proof_submitter_contract_address)
    contract = web3.eth.contract(address=address, abi=load_contract_abi("TaskManager"))
    redis_client = redis.from_url(settings.redis_url, decode_responses=False)
    ProofSubmitter(web3, contract, redis_client, settings.proof_submitter_private_key)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())

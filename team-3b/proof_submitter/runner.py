import asyncio
import signal
import sys

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
    address = web3.to_checksum_address(settings.proof_submitter_contract_address or "")
    contract = web3.eth.contract(address=address, abi=load_contract_abi("TaskManager"))
    redis_client = redis.from_url(settings.redis_url, decode_responses=False)
    submitter = ProofSubmitter(web3, contract, redis_client, settings.proof_submitter_private_key)

    shutdown = asyncio.Event()

    def request_shutdown(sig: signal.Signals) -> None:
        shutdown.set()
        print(f"proof-submitter: received signal={sig}", flush=True)

    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, request_shutdown, sig)

    print("proof-submitter: listening for shutdown signals", flush=True)
    try:
        await shutdown.wait()
    finally:
        # Let queued work land instead of killing mid-batch; the daemon timer
        # will finish on its own once the lock is released.
        print("proof-submitter: draining in-flight batch before exit", flush=True)
        try:
            await submitter.flush()
        except Exception as error:
            print(f"proof-submitter: flush failed: {error}", flush=True)
        try:
            await redis_client.aclose()
        except Exception:
            pass

    print("proof-submitter: stopped", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())

import asyncio
import logging
import signal
import sys

import redis.asyncio as redis
from web3 import Web3

from common.config import get_settings
from common.web3_checks import prover_allowlist_status, signer_address, verify_chain_id
from event_listener.config import load_contract_abi
from proof_submitter.submitter import ProofSubmitter


async def main() -> None:
    """Run the proof-submitter daemon until it receives SIGTERM or SIGINT.

    Before starting, checks the chain ID and, when ``check_prover_allowlist`` is
    set, that the signer is an authorised prover. On shutdown it flushes the
    buffered proofs once before closing Redis.

    Raises:
        SystemExit: With status 1 when the signer is not an authorised prover.
    """
    settings = get_settings().require(
        "web3_rpc_url", "proof_submitter_contract_address", "redis_url"
    )
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    web3 = Web3(Web3.HTTPProvider(settings.web3_rpc_url))
    # Refuse to sign against the wrong chain: Team A's addresses are meaningless
    # on a local Hardhat node, and a silent mismatch would only surface as reverted
    # transactions in the dead-letter queue.
    verify_chain_id(web3, settings.web3_chain_id, "proof-submitter")
    address = web3.to_checksum_address(settings.proof_submitter_contract_address or "")
    contract = web3.eth.contract(
        address=address, abi=load_contract_abi(settings.proof_submitter_artifact)
    )
    signer = signer_address(web3, settings.proof_submitter_private_key, "proof-submitter")

    if settings.check_prover_allowlist:
        allowed = prover_allowlist_status(contract, signer)
        if allowed is False:
            # Team A's TaskManager rejects non-provers with NotProver, so starting
            # would only fill the DLQ. Say exactly what an operator must do instead.
            print(
                f"proof-submitter: signer {signer} is not authorised on "
                f"{address} (TaskManager.isProver == false). Ask Team A to call "
                f"setProver({signer}, true), or set CHECK_PROVER_ALLOWLIST=false to "
                "start anyway.",
                flush=True,
            )
            raise SystemExit(1)
        if allowed is True:
            print(f"proof-submitter: signer {signer} is an authorised prover", flush=True)
        else:
            print(
                f"proof-submitter: contract at {address} exposes no isProver view "
                "(local mock); skipping the allow-list check",
                flush=True,
            )

    redis_client = redis.from_url(settings.redis_url, decode_responses=False)
    submitter = ProofSubmitter(
        web3,
        contract,
        redis_client,
        settings.proof_submitter_private_key,
        signer_address=signer,
        alert_webhook_url=settings.alert_webhook_url,
    )

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
        await redis_client.close()

    print("proof-submitter: stopped", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())

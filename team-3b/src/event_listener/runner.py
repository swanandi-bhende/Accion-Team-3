import asyncio
import logging
import signal

import redis.asyncio as redis

from common.config import get_settings
from common.web3_checks import verify_chain_id
from event_listener.config import load_event_listener_config
from event_listener.listener import EventListener


async def main() -> None:
    """Run the event listener service until SIGTERM or SIGINT.

    Sets up logging, checks the RPC chain id before polling, and closes the Redis
    connection on exit.
    """
    settings = get_settings().require("redis_url")
    # Without a configured handler the listener's INFO lines (startup, dispatch,
    # expiry) never reach `docker compose logs`; only warnings would.
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config = load_event_listener_config()
    # Refuse to follow Team A's addresses on a different chain (for example a
    # leftover local Hardhat node) instead of polling an empty log forever.
    verify_chain_id(config["web3"], settings.web3_chain_id, "event-listener")
    redis_client = redis.from_url(settings.redis_url, decode_responses=False)
    listener = EventListener(
        config["web3"], config["task_manager"], config["escrow"], redis_client
    )

    # SIGTERM (docker stop / compose down) and SIGINT end the poll loop after its
    # current pass; the last processed block is already persisted per block.
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, listener.stop)

    try:
        await listener.start()
    finally:
        await redis_client.close()
    print("event-listener: stopped", flush=True)


if __name__ == "__main__":
    asyncio.run(main())

import asyncio
import signal

import redis.asyncio as redis

from common.config import get_settings
from event_listener.config import load_event_listener_config
from event_listener.listener import EventListener


async def main() -> None:
    settings = get_settings().require("redis_url")
    config = load_event_listener_config()
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

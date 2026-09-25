import asyncio
import os

import redis.asyncio as redis

from event_listener.config import load_event_listener_config
from event_listener.listener import EventListener


async def main() -> None:
    config = load_event_listener_config()
    redis_client = redis.from_url(os.environ["REDIS_URL"], decode_responses=False)
    listener = EventListener(
        config["web3"], config["task_manager"], config["escrow"], redis_client
    )
    await listener.start()


if __name__ == "__main__":
    asyncio.run(main())
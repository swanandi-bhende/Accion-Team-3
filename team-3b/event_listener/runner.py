import asyncio

import redis.asyncio as redis

from common.config import get_settings
from event_listener.config import load_event_listener_config
from event_listener.listener import EventListener


async def main() -> None:
    settings = get_settings()
    config = load_event_listener_config()
    redis_client = redis.from_url(settings.require("redis_url").redis_url, decode_responses=False)
    listener = EventListener(
        config["web3"], config["task_manager"], config["escrow"], redis_client
    )
    await listener.start()


if __name__ == "__main__":
    asyncio.run(main())

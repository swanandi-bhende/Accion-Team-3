import asyncio
import signal
import sys

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
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    shutdown = loop.create_future()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(
            sig,
            lambda s=sig: loop.call_soon_threadsafe(shutdown.set_result, s),
        )

    try:
        loop.run_until_complete(main())
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            loop.close()

    signame = getattr(sys.modules.get("signal"), "Signals", None)
    if signame is not None:
        try:
            pending = signame(shutdown.result()).name if shutdown.done() and not shutdown.cancelled() else ""
        except Exception:
            pending = ""
    print(f"event-listener: stopped (signal={pending or 'exit'})", flush=True)
    sys.exit(0)

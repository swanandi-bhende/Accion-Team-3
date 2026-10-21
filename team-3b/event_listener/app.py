from fastapi import FastAPI

from event_listener.listener import EventListener


def create_status_app(listener: EventListener) -> FastAPI:
    """Build a FastAPI app that serves the listener's status at /listener/status."""
    app = FastAPI(title="Event Listener")

    @app.get("/listener/status")
    async def status() -> dict:
        """Return the listener's last processed block, pending task count and connection state."""
        return await listener.status()

    return app

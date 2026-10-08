from fastapi import FastAPI

from event_listener.listener import EventListener


def create_status_app(listener: EventListener) -> FastAPI:
    app = FastAPI(title="Event Listener")

    @app.get("/listener/status")
    async def status() -> dict:
        return await listener.status()

    return app

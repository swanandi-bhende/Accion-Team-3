import pytest
from httpx import AsyncClient, ASGITransport
from inference.app import app
from contextlib import asynccontextmanager

class MockEngine: pass

@asynccontextmanager
async def mock_lifespan(app_instance):
    print("MOCK LIFESPAN RUNNING")
    app_instance.state.inference_engine = MockEngine()
    yield
    print("MOCK LIFESPAN TEARDOWN")

app.router.lifespan_context = mock_lifespan

@pytest.mark.asyncio
async def test_demo():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        print(f"Inside client, has inference_engine: {hasattr(app.state, 'inference_engine')}")


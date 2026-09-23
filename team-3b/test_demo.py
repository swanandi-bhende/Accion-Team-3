import pytest
from httpx import AsyncClient, ASGITransport
import inference.app as app_module
from inference.app import app

@pytest.fixture
async def override_deps():
    print(f"\nBefore set: {app_module.inference_engine}")
    app_module.inference_engine = "PATCHED_ENGINE"
    print(f"After set: {app_module.inference_engine}")
    yield
    print(f"Teardown: {app_module.inference_engine}")

@pytest.mark.asyncio
async def test_demo(override_deps):
    print(f"In test: {app_module.inference_engine}")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        print(f"In client: {app_module.inference_engine}")

from collections.abc import Callable

import httpx
import pytest

from common.exceptions import StorageUnavailableError
from common.storage import ipfs_backend
from common.storage.ipfs_backend import IPFSBackend

Handler = Callable[[httpx.Request], httpx.Response]


def route_through(monkeypatch: pytest.MonkeyPatch, handler: Handler) -> None:
    """Send every IPFSBackend request to ``handler`` instead of the network."""
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(ipfs_backend.httpx, "AsyncClient", lambda: real_client(transport=transport))


@pytest.mark.asyncio
async def test_upload_returns_the_cid_the_node_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    route_through(monkeypatch, lambda request: httpx.Response(200, json={"Hash": "QmCid"}))

    assert await IPFSBackend("http://ipfs.test").upload(b"model") == "QmCid"


@pytest.mark.asyncio
async def test_retrieve_returns_the_stored_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    route_through(monkeypatch, lambda request: httpx.Response(200, content=b"model"))

    assert await IPFSBackend("http://ipfs.test").retrieve("QmCid") == b"model"


@pytest.mark.parametrize("status", [500, 502, 503])
@pytest.mark.asyncio
async def test_server_errors_are_reported_as_retryable_unavailability(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    route_through(monkeypatch, lambda request: httpx.Response(status))
    backend = IPFSBackend("http://ipfs.test")

    with pytest.raises(StorageUnavailableError):
        await backend.upload(b"model")
    with pytest.raises(StorageUnavailableError):
        await backend.retrieve("QmCid")


@pytest.mark.asyncio
async def test_connection_failures_are_reported_as_retryable_unavailability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    route_through(monkeypatch, refuse)

    with pytest.raises(StorageUnavailableError):
        await IPFSBackend("http://ipfs.test").retrieve("QmCid")

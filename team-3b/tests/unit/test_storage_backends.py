"""Storage backends: the IPFS and 0G adapters, through their HTTP and CLI boundaries."""

import sys
from collections.abc import Callable
from pathlib import Path

import fakeredis.aioredis as fakeredis
import httpx
import pytest
from pydantic import SecretStr

from common.config import Settings
from common.exceptions import ConfigurationError, StorageUnavailableError
from common.interfaces import StorageBackend
from common.model_registry import ModelRegistry
from common.storage import ipfs_backend
from common.storage.factory import build_storage_backend
from common.storage.ipfs_backend import IPFSBackend
from common.storage.zerog_backend import CID_PREFIX, ZeroGStorageBackend
from verification.resolver import StorageProofResolver

Handler = Callable[[httpx.Request], httpx.Response]


def route_through(monkeypatch: pytest.MonkeyPatch, handler: Handler) -> None:
    """Send every IPFSBackend request to ``handler`` instead of the network."""
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        ipfs_backend.httpx, "AsyncClient", lambda: real_client(transport=transport)
    )


@pytest.mark.asyncio
async def test_upload_returns_the_cid_the_node_reports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route_through(
        monkeypatch, lambda request: httpx.Response(200, json={"Hash": "QmCid"})
    )

    assert await IPFSBackend("http://ipfs.test").upload(b"model") == "QmCid"


@pytest.mark.asyncio
async def test_retrieve_returns_the_stored_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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


KEY = "0x" + "11" * 32

REAL_BALANCE = ZeroGStorageBackend.balance_wei

WELL_FUNDED_WEI = 100 * 10**18


@pytest.fixture(autouse=True)
def well_funded(monkeypatch: pytest.MonkeyPatch) -> None:
    """The balance check reads the RPC, so every test gets a funded wallet unless it says otherwise."""

    async def balance(self: ZeroGStorageBackend) -> int:
        return WELL_FUNDED_WEI

    monkeypatch.setattr(ZeroGStorageBackend, "balance_wei", balance)


FAKE_CLI = """import hashlib, os, pathlib, shutil, sys, time

args = sys.argv[1:]
store = pathlib.Path(os.environ["FAKE_STORE"])
mode = os.environ.get("FAKE_MODE", "ok")


def value(flag):
    return args[args.index(flag) + 1]


if mode == "fail":
    print("error: insufficient funds for gas (key " + value("--key") + ")", file=sys.stderr)
    sys.exit(1)
if mode == "silent":
    sys.exit(0)
if mode == "hang":
    time.sleep(60)
if args[0] == "upload":
    data = pathlib.Path(value("--file")).read_bytes()
    root = "0x" + hashlib.sha256(data).hexdigest()
    (store / root).write_bytes(data)
    print('time=2026 level=info msg="file uploaded, root = ' + root + '"', file=sys.stderr)
elif args[0] == "download":
    source = store / value("--root")
    if not source.exists():
        sys.exit(2)
    shutil.copyfile(source, value("--file"))
"""


@pytest.fixture
def fake_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    store = tmp_path / "store"
    store.mkdir()
    script = tmp_path / "fake_cli.py"
    script.write_text(FAKE_CLI)
    # A shell wrapper, because a shebang cannot hold the interpreter path when it has spaces.
    cli = tmp_path / "0g-storage-client"
    cli.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
    cli.chmod(0o755)
    monkeypatch.setenv("FAKE_STORE", str(store))
    monkeypatch.setenv("FAKE_MODE", "ok")
    return cli


def backend(cli: Path, timeout_s: float = 30.0) -> ZeroGStorageBackend:
    return ZeroGStorageBackend(
        cli=str(cli),
        rpc_url="https://rpc.test",
        indexer_url="https://indexer.test",
        private_key=KEY,
        timeout_s=timeout_s,
    )


@pytest.mark.asyncio
async def test_upload_then_retrieve_round_trips_and_uses_a_zg_cid(
    fake_cli: Path,
) -> None:
    storage = backend(fake_cli)

    cid = await storage.upload(b"model-bytes")

    assert cid.startswith(CID_PREFIX + "0x")
    assert len(cid) == len(CID_PREFIX) + 66
    assert await storage.retrieve(cid) == b"model-bytes"


@pytest.mark.asyncio
async def test_a_failed_upload_is_retryable_and_hides_the_key(
    fake_cli: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_MODE", "fail")

    with pytest.raises(StorageUnavailableError) as info:
        await backend(fake_cli).upload(b"model-bytes")

    assert KEY not in str(info.value)
    assert "<redacted>" in str(info.value)


@pytest.mark.asyncio
async def test_an_upload_that_reports_no_root_hash_is_retryable(
    fake_cli: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_MODE", "silent")

    with pytest.raises(StorageUnavailableError, match="without reporting a root hash"):
        await backend(fake_cli).upload(b"model-bytes")


@pytest.mark.asyncio
async def test_a_hung_cli_times_out_as_an_outage(
    fake_cli: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_MODE", "hang")

    with pytest.raises(StorageUnavailableError, match="timed out"):
        await backend(fake_cli, timeout_s=0.5).upload(b"model-bytes")


@pytest.mark.asyncio
async def test_retrieve_rejects_a_cid_that_is_not_a_zg_cid(fake_cli: Path) -> None:
    with pytest.raises(ValueError, match="not a 0G Storage CID"):
        await backend(fake_cli).retrieve("QmNotZeroG")


@pytest.mark.asyncio
async def test_a_missing_download_is_retryable(fake_cli: Path) -> None:
    with pytest.raises(StorageUnavailableError):
        await backend(fake_cli).retrieve(CID_PREFIX + "0x" + "ab" * 32)


@pytest.mark.asyncio
async def test_the_resolver_reads_zg_references_from_storage(fake_cli: Path) -> None:
    storage = backend(fake_cli)
    cid = await storage.upload(b"proof-bytes")

    assert await StorageProofResolver(storage).resolve(cid) == b"proof-bytes"


def test_ipfs_is_the_default_backend() -> None:
    settings = Settings(
        model_storage_backend="ipfs", model_storage_url="http://ipfs.test"
    )

    assert isinstance(build_storage_backend(settings), IPFSBackend)


def test_the_factory_builds_zerog_from_settings(fake_cli: Path) -> None:
    settings = Settings(
        model_storage_backend="0g",
        zerog_cli=str(fake_cli),
        zerog_rpc_endpoint="evmrpc-testnet.0g.ai",
        zerog_private_key=SecretStr(KEY),
    )

    storage = build_storage_backend(settings)

    assert isinstance(storage, ZeroGStorageBackend)
    assert storage.rpc_url == "https://evmrpc-testnet.0g.ai"


def test_the_factory_refuses_a_missing_cli(fake_cli: Path) -> None:
    settings = Settings(
        model_storage_backend="0g",
        zerog_cli="no-such-0g-cli",
        zerog_rpc_endpoint="evmrpc-testnet.0g.ai",
        zerog_private_key=SecretStr(KEY),
    )

    with pytest.raises(ConfigurationError, match="binary on PATH"):
        build_storage_backend(settings)


def test_the_factory_requires_the_rpc_endpoint_and_key(fake_cli: Path) -> None:
    # Explicit None overrides the values in .env, so the test does not depend on the local file.
    settings = Settings(
        model_storage_backend="0g",
        zerog_cli=str(fake_cli),
        zerog_rpc_endpoint=None,
        zerog_private_key=None,
    )

    with pytest.raises(ConfigurationError, match="ZEROG_RPC_ENDPOINT"):
        build_storage_backend(settings)


def test_the_factory_refuses_a_malformed_key(fake_cli: Path) -> None:
    settings = Settings(
        model_storage_backend="0g",
        zerog_cli=str(fake_cli),
        zerog_rpc_endpoint="evmrpc-testnet.0g.ai",
        zerog_private_key=SecretStr("0xnothex"),
    )

    with pytest.raises(ConfigurationError, match="not a valid private key"):
        build_storage_backend(settings)


@pytest.mark.asyncio
async def test_an_unfunded_wallet_is_refused_without_running_the_cli(
    fake_cli: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def empty(self: ZeroGStorageBackend) -> int:
        return 0

    monkeypatch.setattr(ZeroGStorageBackend, "balance_wei", empty)

    with pytest.raises(ConfigurationError, match="below the"):
        await backend(fake_cli).upload(b"model-bytes")
    assert list((fake_cli.parent / "store").iterdir()) == []


@pytest.mark.asyncio
async def test_an_unreachable_rpc_is_retryable(fake_cli: Path) -> None:
    storage = ZeroGStorageBackend(
        cli=str(fake_cli),
        rpc_url="http://127.0.0.1:9",
        indexer_url="https://indexer.test",
        private_key=KEY,
    )

    with pytest.raises(StorageUnavailableError, match="balance check failed"):
        await REAL_BALANCE(storage)


class UnfundedStorage(StorageBackend):
    async def upload(self, data: bytes) -> str:
        raise ConfigurationError("wallet is unfunded")

    async def retrieve(self, cid: str) -> bytes:
        raise NotImplementedError


@pytest.mark.asyncio
async def test_the_model_registry_does_not_retry_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    delays: list[float] = []

    async def record(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("common.model_registry.asyncio.sleep", record)
    registry = ModelRegistry(
        fakeredis.FakeRedis(decode_responses=False),
        UnfundedStorage(),
        spool_dir=tmp_path,
    )

    with pytest.raises(ConfigurationError):
        await registry.upload_model("unfunded_model", b"model-bytes")
    assert delays == []

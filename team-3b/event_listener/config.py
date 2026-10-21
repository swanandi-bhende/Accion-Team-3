"""Web3 configuration and mock-contract ABI loading."""

import json
import logging
from pathlib import Path
from typing import Any

from web3 import Web3

from common.config import get_settings

ROOT = Path(__file__).resolve().parents[1]

logger = logging.getLogger(__name__)


def _websocket_provider_factory() -> Any:
    """A *synchronous* WebSocket provider, if this web3 build ships one.

    web3 >= 7 only exposes an asynchronous, persistent ``WebSocketProvider``,
    which the synchronous ``Web3`` client rejects. The listener polls with
    filters and ``get_all_entries`` rather than subscribing, so when no
    synchronous provider exists the transport falls back to HTTP instead of
    refusing to start.
    """
    factory = getattr(Web3, "WebsocketProvider", None)
    if factory is not None:
        return factory
    try:
        from web3.providers import websocket
    except ImportError:
        return None
    return getattr(websocket, "WebSocketProvider", None)


def build_provider(websocket_url: str) -> Any:
    """Build the provider for ``websocket_url``, degrading to its HTTP twin."""
    factory = _websocket_provider_factory()
    if factory is not None:
        return factory(websocket_url)
    rpc_url = get_settings().web3_rpc_url or websocket_url.replace(
        "wss://", "https://"
    ).replace("ws://", "http://")
    logger.warning(
        "No synchronous WebSocket provider in this web3 build; polling %s over HTTP instead of %s",
        rpc_url,
        websocket_url,
    )
    return Web3.HTTPProvider(rpc_url)


def contract_artifact_dir() -> Path:
    """Directory of compiled ABIs, from CONTRACT_ARTIFACTS_DIR (relative to the repo root)."""
    configured = Path(get_settings().contract_artifacts_dir)
    return configured if configured.is_absolute() else ROOT / configured


def artifact_candidates(contract_name: str, directory: Path) -> list[Path]:
    """Every accepted artifact filename, in preference order.

    The mock contracts are compiled by Hardhat, which nests artifacts as
    ``<Name>.sol/<Name>.json``. Team A ships bare ABI exports named
    ``<Name>.abi.json``, so an integration only has to point
    ``CONTRACT_ARTIFACTS_DIR`` at their export directory.
    """
    return [
        directory / f"{contract_name}.sol" / f"{contract_name}.json",
        directory / f"{contract_name}.abi.json",
        directory / f"{contract_name}.json",
    ]


def load_contract_abi(contract_name: str, artifact_dir: Path | None = None) -> list[dict[str, Any]]:
    """Read a contract ABI from a Hardhat artifact tree or a bare ABI JSON export."""
    directory = artifact_dir or contract_artifact_dir()
    candidates = artifact_candidates(contract_name, directory)
    for path in candidates:
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as artifact_file:
            payload = json.load(artifact_file)
        abi = payload["abi"] if isinstance(payload, dict) else payload
        if not isinstance(abi, list):
            raise ValueError(f"{path}: expected an ABI list or an artifact with an 'abi' key")
        return abi
    searched = ", ".join(str(path) for path in candidates)
    raise FileNotFoundError(
        f"No ABI artifact for {contract_name} in {directory}. Searched: {searched}. "
        "Set CONTRACT_ARTIFACTS_DIR and the *_ARTIFACT name variables to match your export."
    )


def load_event_listener_config(
    websocket_url: str | None = None,
    task_manager_address: str | None = None,
    escrow_address: str | None = None,
    artifact_dir: Path | None = None,
) -> dict[str, Any]:
    """Load swappable contract metadata and create the WebSocket Web3 client."""
    settings = get_settings().require(
        "web3_ws_url", "task_manager_address", "escrow_address"
    )
    websocket_url = websocket_url or settings.web3_ws_url or ""
    task_manager_address = task_manager_address or settings.task_manager_address or ""
    escrow_address = escrow_address or settings.escrow_address or ""
    web3 = Web3(build_provider(websocket_url))
    return {
        "web3": web3,
        "task_manager": web3.eth.contract(
            address=web3.to_checksum_address(task_manager_address),
            abi=load_contract_abi(settings.task_manager_artifact, artifact_dir),
        ),
        "escrow": web3.eth.contract(
            address=web3.to_checksum_address(escrow_address),
            abi=load_contract_abi(settings.escrow_artifact, artifact_dir),
        ),
    }

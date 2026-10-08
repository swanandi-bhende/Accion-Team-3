"""Web3 configuration and mock-contract ABI loading."""

import json
import logging
from pathlib import Path
from typing import Any

from web3 import Web3

from common.config import get_settings

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = ROOT / "mocks" / "contracts" / "artifacts" / "contracts"

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
        from web3.providers.websocket import WebSocketProvider

        return WebSocketProvider
    except ImportError:
        return None


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


def load_contract_abi(contract_name: str, artifact_dir: Path = ARTIFACT_DIR) -> list[dict[str, Any]]:
    artifact_path = artifact_dir / f"{contract_name}.sol" / f"{contract_name}.json"
    with artifact_path.open(encoding="utf-8") as artifact_file:
        return json.load(artifact_file)["abi"]


def load_event_listener_config(
    websocket_url: str | None = None,
    task_manager_address: str | None = None,
    escrow_address: str | None = None,
    artifact_dir: Path = ARTIFACT_DIR,
) -> dict[str, Any]:
    """Load swappable contract metadata and create the WebSocket Web3 client."""
    settings = get_settings().require(
        "web3_ws_url", "task_manager_address", "escrow_address"
    )
    websocket_url = websocket_url or settings.web3_ws_url
    task_manager_address = task_manager_address or settings.task_manager_address
    escrow_address = escrow_address or settings.escrow_address
    web3 = Web3(build_provider(websocket_url))
    return {
        "web3": web3,
        "task_manager": web3.eth.contract(
            address=web3.to_checksum_address(task_manager_address),
            abi=load_contract_abi("TaskManager", artifact_dir),
        ),
        "escrow": web3.eth.contract(
            address=web3.to_checksum_address(escrow_address),
            abi=load_contract_abi("Escrow", artifact_dir),
        ),
    }

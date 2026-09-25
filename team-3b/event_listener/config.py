"""Web3 configuration and mock-contract ABI loading."""

import json
import os
from pathlib import Path
from typing import Any

from web3 import Web3


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = ROOT / "mocks" / "contracts" / "artifacts" / "contracts"


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
    websocket_url = websocket_url or os.environ["WEB3_WS_URL"]
    task_manager_address = task_manager_address or os.environ["TASK_MANAGER_ADDRESS"]
    escrow_address = escrow_address or os.environ["ESCROW_ADDRESS"]
    web3_class = Web3
    provider_factory = getattr(web3_class, "WebsocketProvider", None)
    if provider_factory is None:
        from web3.providers.websocket import WebSocketProvider

        provider_factory = WebSocketProvider
    web3 = web3_class(provider_factory(websocket_url))
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
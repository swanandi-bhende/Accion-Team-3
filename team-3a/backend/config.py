"""Central configuration, read from the environment (see .env.example)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env", override=True)


@dataclass
class Settings:
    web3_provider_url: str = os.getenv("WEB3_PROVIDER_URL", "http://127.0.0.1:8545")
    # Req 7.3: reads return within 2 seconds. Applied to read calls only; submissions use
    # the longer general timeout so a slow node does not fail a valid transaction.
    rpc_read_timeout_seconds: float = float(os.getenv("RPC_READ_TIMEOUT_SECONDS", "2"))
    rpc_timeout_seconds: float = float(os.getenv("RPC_TIMEOUT_SECONDS", "30"))
    ipfs_api_url: str = os.getenv("IPFS_API_URL", "http://127.0.0.1:5001")
    storage_timeout_seconds: float = float(os.getenv("STORAGE_TIMEOUT_SECONDS", "30"))

    nebula_host: str = os.getenv("NEBULA_HOST", "127.0.0.1")
    nebula_port: int = int(os.getenv("NEBULA_PORT", "9669"))
    nebula_user: str = os.getenv("NEBULA_USER", "root")
    nebula_password: str = os.getenv("NEBULA_PASSWORD", "nebula")
    nebula_space: str = os.getenv("NEBULA_SPACE", "legal")

    graph_query_timeout_seconds: float = float(os.getenv("GRAPH_QUERY_TIMEOUT_SECONDS", "3"))
    search_query_timeout_seconds: float = float(os.getenv("SEARCH_QUERY_TIMEOUT_SECONDS", "2"))
    weaviate_url: str = os.getenv("WEAVIATE_URL", "http://127.0.0.1:8081")
    relevance_threshold: float = float(os.getenv("RELEVANCE_THRESHOLD", "0.5"))

    deployments_file: Path = field(
        default_factory=lambda: REPO_ROOT
        / "deployments"
        / f"{os.getenv('DEPLOYMENT_NETWORK', 'hardhat')}.json"
    )

    def load_addresses(self) -> dict[str, str]:
        """Reads contract addresses written by deploy/deploy.ts."""
        if not self.deployments_file.exists():
            raise FileNotFoundError(
                f"No deployment manifest at {self.deployments_file}. "
                "Run `npx hardhat run deploy/deploy.ts --network localhost` first."
            )
        return json.loads(self.deployments_file.read_text())


settings = Settings()

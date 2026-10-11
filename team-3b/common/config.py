"""Single source of truth for environment configuration.

Every environment variable the subsystem reads is declared once here on one typed
:class:`Settings` model, so there is exactly one place to look when asking "what
does this system need?". Module-level ``os.getenv`` calls are gone.

Presence is validated **per service**, not globally. A federated-learning worker
needs no blockchain configuration and the inference engine needs no contract
addresses, so requiring every variable in every process would force each
container to carry a bloated environment -- and, worse, would make an unrelated
missing variable crash a service that never reads it. Instead each entrypoint
calls ``get_settings().require(...)`` with exactly the variables it uses, and all
missing or blank ones are reported together in a single itemized error.
"""

import json
from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from common.exceptions import ConfigurationError

MOCK_ADDRESS_KEYS = (
    "TASK_MANAGER_ADDRESS",
    "ESCROW_ADDRESS",
    "PROOF_SUBMITTER_CONTRACT_ADDRESS",
)


class Settings(BaseSettings):
    # --- TEE ---
    tee_mode: str | None = None
    tee_sealed_key_path: str | None = None
    tee_trusted_keys_path: str | None = None
    tee_inference_timeout_s: float = 30.0

    # --- Storage ---
    redis_url: str | None = None
    model_storage_backend: str = "ipfs"
    model_storage_url: str | None = None
    model_max_size_mb: int = 500
    zerog_rpc_endpoint: str = "https://rpc-testnet.0g.ai"
    zerog_private_key: str = ""

    # --- Blockchain ---
    web3_ws_url: str | None = None
    web3_rpc_url: str | None = None
    task_manager_address: str | None = None
    escrow_address: str | None = None
    mock_deployments_path: str | None = None

    # --- Proof submission ---
    proof_batch_window_s: float = 10.0
    proof_submitter_private_key: str = ""
    proof_submitter_contract_address: str | None = None

    # --- zkML ---
    zkml_proving_key_store: str = "data/proving_keys"
    zkml_supported_architectures: str | None = None

    # --- Federated learning ---
    fl_task_id: str = "fl-demo-task"
    fl_num_clients: int = 3
    fl_num_rounds: int = 3
    fl_aggregation_strategy: str = "fedavg"
    fl_model_id: str = "fl-demo-model"
    fl_model_architecture: str = "mlp"
    fl_round_timeout: float = 300.0

    # --- Observability ---
    log_level: str = "INFO"

    # --- Pipeline ---
    pipeline_max_in_flight: int = 4

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @model_validator(mode="after")
    def _adopt_mock_deployments(self) -> "Settings":
        """Fill blank contract addresses from the mock deploy output.

        ``docker compose up`` cannot know the mock addresses when it creates the
        consumer containers, because the deploy step writes them afterwards. With
        ``MOCK_DEPLOYMENTS_PATH`` pointing at that file, a first ``up`` therefore
        resolves them without anyone copying values into ``.env``. An explicitly
        configured environment variable always wins.
        """
        if not self.mock_deployments_path:
            return self
        deployments_file = Path(self.mock_deployments_path)
        if not deployments_file.exists():
            return self
        deployments = json.loads(deployments_file.read_text(encoding="utf-8"))
        for key in MOCK_ADDRESS_KEYS:
            field = key.lower()
            if not getattr(self, field, None) and deployments.get(key):
                setattr(self, field, deployments[key])
        return self

    def require(self, *names: str) -> "Settings":
        """Fail fast unless every named setting has a non-empty value.

        All offenders are reported in one itemized error, so a deployment can be
        corrected in a single pass.
        """
        missing = [name for name in names if not getattr(self, name, None)]
        if missing:
            listing = "\n".join(f"  - {name}" for name in missing)
            raise ConfigurationError(
                "Missing required configuration for this service:\n"
                f"{listing}\n"
                "Populate these in .env (see .env.example) or the process environment."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    try:
        return Settings()
    except ValueError as error:
        raise ConfigurationError(
            f"Invalid environment configuration:\n{error}"
        ) from error

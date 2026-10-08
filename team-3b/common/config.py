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

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings

from common.exceptions import ConfigurationError


class Settings(BaseSettings):
    # --- TEE ---
    tee_mode: str | None = Field(None, env="TEE_MODE")
    tee_sealed_key_path: str | None = Field(None, env="TEE_SEALED_KEY_PATH")
    tee_trusted_keys_path: str | None = Field(None, env="TEE_TRUSTED_KEYS_PATH")
    tee_inference_timeout_s: float = Field(30.0, env="TEE_INFERENCE_TIMEOUT_S")

    # --- Storage ---
    redis_url: str | None = Field(None, env="REDIS_URL")
    model_storage_backend: str = Field("ipfs", env="MODEL_STORAGE_BACKEND")
    model_storage_url: str | None = Field(None, env="MODEL_STORAGE_URL")
    model_max_size_mb: int = Field(500, env="MODEL_MAX_SIZE_MB")
    zerog_rpc_endpoint: str = Field("https://rpc-testnet.0g.ai", env="ZEROG_RPC_ENDPOINT")
    zerog_private_key: str = Field("", env="ZEROG_PRIVATE_KEY")

    # --- Blockchain ---
    web3_ws_url: str | None = Field(None, env="WEB3_WS_URL")
    web3_rpc_url: str | None = Field(None, env="WEB3_RPC_URL")
    task_manager_address: str | None = Field(None, env="TASK_MANAGER_ADDRESS")
    escrow_address: str | None = Field(None, env="ESCROW_ADDRESS")

    # --- Proof submission ---
    proof_batch_window_s: float = Field(10.0, env="PROOF_BATCH_WINDOW_S")
    proof_submitter_private_key: str = Field("", env="PROOF_SUBMITTER_PRIVATE_KEY")
    proof_submitter_contract_address: str | None = Field(
        None, env="PROOF_SUBMITTER_CONTRACT_ADDRESS"
    )

    # --- zkML ---
    zkml_proving_key_store: str = Field("data/proving_keys", env="ZKML_PROVING_KEY_STORE")
    zkml_supported_architectures: str | None = Field(None, env="ZKML_SUPPORTED_ARCHITECTURES")

    # --- Federated learning ---
    fl_task_id: str = Field("fl-demo-task", env="FL_TASK_ID")
    fl_num_clients: int = Field(3, env="FL_NUM_CLIENTS")
    fl_num_rounds: int = Field(3, env="FL_NUM_ROUNDS")
    fl_aggregation_strategy: str = Field("fedavg", env="FL_AGGREGATION_STRATEGY")
    fl_model_id: str = Field("fl-demo-model", env="FL_MODEL_ID")
    fl_model_architecture: str = Field("mlp", env="FL_MODEL_ARCHITECTURE")
    fl_round_timeout: float = Field(300.0, env="FL_ROUND_TIMEOUT")

    # --- Observability ---
    log_level: str = Field("INFO", env="LOG_LEVEL")

    # --- Pipeline ---
    pipeline_max_in_flight: int = Field(4, env="PIPELINE_MAX_IN_FLIGHT")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

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

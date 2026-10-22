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
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from common.exceptions import ConfigurationError

DEPLOYMENT_ADDRESS_KEYS = (
    "TASK_MANAGER_ADDRESS",
    "ESCROW_ADDRESS",
    "PROOF_SUBMITTER_CONTRACT_ADDRESS",
)
# The all-zero address is the placeholder shipped in .env.example. It means "not
# configured yet", so a deployment record replaces it.
PLACEHOLDER_ADDRESS = "0x" + "0" * 40
# Same idea for keys: an all-zero key is a placeholder, not a signer. Treating it
# as one makes eth_account refuse to sign (and the submitter crash at startup).
PLACEHOLDER_PRIVATE_KEY = "0x" + "0" * 64


class Settings(BaseSettings):
    """Typed view of every environment variable the subsystem reads.

    Values come from the process environment or ``.env``. Each entrypoint calls
    :meth:`require` to check the variables it needs.
    """

    # --- TEE ---
    tee_mode: str | None = None
    tee_trusted_keys_path: str | None = None
    tee_inference_timeout_s: float = 30.0
    # 64 hex characters (a 256-bit AES key) that open sealed models. Blank means each
    # engine generates its own key, which is only useful for models sealed in-process.
    tee_sealed_key: str | None = None

    # --- Storage ---
    redis_url: str | None = None
    model_storage_url: str | None = None

    # --- Blockchain ---
    web3_ws_url: str | None = None
    web3_rpc_url: str | None = None
    # Expected chain id. When set, the services refuse to start against a different
    # chain instead of sending proofs into a local node that happens to answer.
    web3_chain_id: int | None = None
    task_manager_address: str | None = None
    escrow_address: str | None = None
    mock_deployments_path: str | None = None
    # Directory of contract ABIs. Both layouts are read: the Hardhat artifact
    # tree (<Name>.sol/<Name>.json) and bare ABI exports (<Name>.abi.json).
    # Team A's committed exports live in team_a_assets/, so this is the only
    # input needed to read their ABIs. Relative paths resolve from the repo root.
    contract_artifacts_dir: str = "team_a_assets"
    # Artifact base names. Team A names the escrow EscrowContract, matching the
    # exports and the deployment record.
    task_manager_artifact: str = "TaskManager"
    escrow_artifact: str = "EscrowContract"
    proof_submitter_artifact: str = "TaskManager"
    # Team A's TaskManager gates submitProofs on a Prover allow-list. Refusing to
    # start when the signer is not on it turns a silent revert-and-DLQ loop into an
    # immediate, actionable failure.
    check_prover_allowlist: bool = True

    # --- Proof submission ---
    # Off: development-commitment proofs are not zero-knowledge and are never sent on chain.
    # Only a test environment should set this, to exercise the chain path.
    proof_allow_development_backend: bool = False
    proof_batch_window_s: float = 10.0
    proof_submitter_private_key: str = ""
    proof_submitter_contract_address: str | None = None

    # --- zkML ---
    # "ezkl" is production: zero-knowledge proofs from the model's circuit. It needs the
    # ezkl package (requirements/zkml.txt) and a proving bundle per model in the store.
    # "development" is the commitment stand-in, which is never submitted on chain.
    zkml_backend: Literal["development", "ezkl"] = "development"
    zkml_proving_key_store: str = "data/proving_keys"

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

    @field_validator("tee_sealed_key", mode="before")
    @classmethod
    def _blank_sealed_key_is_unset(cls, value: object) -> object:
        """Validate the sealed key's shape at startup, so a bad key fails before serving."""
        if isinstance(value, str) and not value.strip():
            return None
        if isinstance(value, str):
            try:
                key = bytes.fromhex(value.removeprefix("0x"))
            except ValueError as error:
                raise ValueError("TEE_SEALED_KEY must be hex") from error
            if len(key) != 32:
                raise ValueError("TEE_SEALED_KEY must be 64 hex characters (a 256-bit key)")
        return value

    def sealed_key_bytes(self) -> bytes | None:
        """The configured sealing key as bytes, or None when it is unset."""
        if self.tee_sealed_key is None:
            return None
        return bytes.fromhex(self.tee_sealed_key.removeprefix("0x"))

    @field_validator("web3_chain_id", mode="before")
    @classmethod
    def _blank_chain_id_is_unset(cls, value: object) -> object:
        """Treat ``WEB3_CHAIN_ID=`` (the .env.example default) as "no check"."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("proof_submitter_private_key", mode="before")
    @classmethod
    def _placeholder_key_is_unset(cls, value: object) -> object:
        """The zero key in .env.example means "no signer", not "sign with zeros".

        Without this, the local mock (which signs with the node's unlocked account)
        and a real deployment (which supplies a funded key) both break: eth_account
        refuses an all-zero key, and the submitter crashes on the first batch.
        """
        if isinstance(value, str) and value.strip().lower() in ("", PLACEHOLDER_PRIVATE_KEY):
            return ""
        return value

    @model_validator(mode="after")
    def _adopt_deployment_record(self) -> "Settings":
        """Replace blank or placeholder contract addresses with a deployment record.

        Compose points ``MOCK_DEPLOYMENTS_PATH`` (a historical name) at Team A's
        ``deployments/galileo.json``, so a fresh checkout resolves their addresses
        without anyone copying values into ``.env``. An address that was configured
        explicitly always wins.
        """
        if not self.mock_deployments_path:
            return self
        deployments_file = Path(self.mock_deployments_path)
        if not deployments_file.exists():
            return self
        deployments = json.loads(deployments_file.read_text(encoding="utf-8"))
        for key in DEPLOYMENT_ADDRESS_KEYS:
            field = key.lower()
            current = getattr(self, field, None)
            if current in (None, "", PLACEHOLDER_ADDRESS) and deployments.get(key):
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
    """Return the process-wide Settings instance, built once and cached.

    Raises:
        ConfigurationError: If the environment or ``.env`` holds an invalid value.
    """
    try:
        return Settings()
    except ValueError as error:
        raise ConfigurationError(
            f"Invalid environment configuration:\n{error}"
        ) from error

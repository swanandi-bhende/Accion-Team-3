"""Build the model storage backend that the settings name."""

import shutil

from common.config import Settings
from common.exceptions import ConfigurationError
from common.interfaces import StorageBackend
from common.storage.ipfs_backend import IPFSBackend
from common.storage.zerog_backend import ZeroGStorageBackend


def build_storage_backend(settings: Settings) -> StorageBackend:
    """IPFS by default. With ``MODEL_STORAGE_BACKEND=0g``, 0G Storage through its CLI.

    A missing CLI or credential is a configuration error, raised here at start-up. It is not
    left to the upload path, where it would look like an outage and be retried forever.

    Raises:
        ConfigurationError: If 0G is selected but its CLI, RPC endpoint or key is missing.
    """
    if settings.model_storage_backend == "ipfs":
        return IPFSBackend(node_url=settings.model_storage_url or "http://localhost:5001")
    if shutil.which(settings.zerog_cli) is None:
        raise ConfigurationError(f"0G storage needs the {settings.zerog_cli} binary on PATH (set ZEROG_CLI to its path)")
    key = settings.zerog_private_key.get_secret_value() if settings.zerog_private_key else ""
    if not settings.zerog_rpc_endpoint or not key:
        raise ConfigurationError("0G storage needs ZEROG_RPC_ENDPOINT and ZEROG_PRIVATE_KEY")
    rpc = settings.zerog_rpc_endpoint
    try:
        return ZeroGStorageBackend(
            cli=settings.zerog_cli,
            rpc_url=rpc if rpc.startswith("http") else f"https://{rpc}",
            indexer_url=settings.zerog_indexer_url,
            private_key=key,
            min_balance_0g=settings.zerog_min_balance,
        )
    except ValueError as error:
        raise ConfigurationError("ZEROG_PRIVATE_KEY is not a valid private key") from error

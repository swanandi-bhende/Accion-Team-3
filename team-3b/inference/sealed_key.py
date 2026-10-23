"""The sealing key for encrypted models, kept in a file rather than in the environment.

The file holds 32 raw bytes and is created with mode 0600 on first use. It is still plaintext
on the host: this protects it from other users, not from the host operator. Hardware sealing
needs a real TEE (see docs/CHECKPOINTS.md).
"""

import logging
import os
from pathlib import Path

from common.exceptions import ConfigurationError

logger = logging.getLogger(__name__)
KEY_BYTES = 32


def load_sealed_key(path: str | None) -> bytes | None:
    """The key stored at ``path``, created there on first use.

    Returns None when no path is configured. The engine then generates a key for each process,
    so a model sealed under it cannot be opened after a restart. A warning says so.

    Raises:
        ConfigurationError: If the file exists but does not hold exactly 32 bytes.
    """
    if not path:
        logger.warning("TEE_SEALED_KEY_PATH is unset: encrypted models cannot be opened after a restart")
        return None
    key_path = Path(path)
    if key_path.exists():
        return _read_key(key_path)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    # Write the key to a temporary file first, then link it into place. Linking fails if another
    # process created the file first, so two services starting together cannot read a partial key.
    temporary = key_path.with_name(f".{key_path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    key = os.urandom(KEY_BYTES)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(key)
    try:
        os.link(temporary, key_path)
    except FileExistsError:
        return _read_key(key_path)
    finally:
        temporary.unlink(missing_ok=True)
    logger.warning("created sealing key at %s; back it up, encrypted models cannot be opened without it", key_path)
    return key


def _read_key(key_path: Path) -> bytes:
    key = key_path.read_bytes()
    if len(key) != KEY_BYTES:
        raise ConfigurationError(f"{key_path} must hold exactly {KEY_BYTES} raw bytes, found {len(key)}")
    return key

"""Startup checks shared by the on-chain services.

These exist because the same code now runs against two very different chains: the
local Hardhat mock (chain id 31337, unlocked accounts, permissive contracts) and
Team A's Galileo testnet (chain id 16602, a funded signer, a Prover allow-list).
Each check turns a class of silent misconfiguration into a named startup failure.
"""

import logging
from typing import Any

from common.exceptions import ChainMismatchError, ConfigurationError

logger = logging.getLogger(__name__)


def chain_id(web3: Any) -> int | None:
    """Return the RPC's chain id, or ``None`` when the transport cannot report one."""
    try:
        return int(web3.eth.chain_id)
    except Exception:  # noqa: BLE001 - any transport failure means "unknown"
        return None


def verify_chain_id(web3: Any, expected: int | None, service: str) -> int | None:
    """Fail when the endpoint serves a chain other than ``expected``.

    ``expected`` is optional so local development keeps working without extra
    configuration; the check only runs when ``WEB3_CHAIN_ID`` is set.
    """
    actual = chain_id(web3)
    if actual is None:
        logger.warning("%s: could not read chain id from the RPC endpoint", service)
        return None
    if expected is not None and actual != expected:
        raise ChainMismatchError(service, expected, actual, _rpc_hint(web3))
    logger.info("%s: connected to chain id %s", service, actual)
    return actual


def _rpc_hint(web3: Any) -> str | None:
    provider = getattr(web3, "provider", None)
    endpoint = getattr(provider, "endpoint_uri", None)
    return str(endpoint) if endpoint else None


def signer_address(web3: Any, private_key: str, service: str) -> str | None:
    """Resolve the address that signs transactions for this service.

    A real network has no unlocked accounts, so a configured private key is the
    only way to know the sender before the first transaction -- and the allow-list
    check needs it before then. Falls back to the node's first account, which is
    what the local Hardhat mock provides.
    """
    key = private_key.strip()
    # An all-zero key is the shipped placeholder; treat it as "no key" rather than
    # raising a confusing secp256k1 length error.
    if key and not set(key.removeprefix("0x")) <= {"0"}:
        from eth_account import Account

        try:
            return str(Account.from_key(key).address)
        except (ValueError, TypeError) as error:
            raise ConfigurationError(
                f"{service}: PROOF_SUBMITTER_PRIVATE_KEY is not a valid secp256k1 private key: {error}"
            ) from error
    accounts = getattr(getattr(web3, "eth", None), "accounts", None) or []
    if accounts:
        return str(accounts[0])
    return None


def prover_allowlist_status(contract: Any, address: str | None) -> bool | None:
    """Whether ``address`` may call ``submitProofs``, or ``None`` if unknowable.

    ``None`` means the contract has no ``isProver`` view (the local mock) or no
    signer could be resolved, so the caller should log and continue.
    """
    is_prover = getattr(getattr(contract, "functions", None), "isProver", None)
    if is_prover is None or address is None:
        return None
    try:
        return bool(is_prover(address).call())
    except Exception as error:  # noqa: BLE001 - ABI/address mismatch must not crash startup
        logger.warning("Could not read isProver(%s): %s", address, error)
        return None

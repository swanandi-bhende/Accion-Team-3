"""The SettlementContract the proof submitter calls after a proof is accepted."""

from typing import Any

from common.config import Settings
from common.exceptions import ConfigurationError
from event_listener.config import load_contract_abi


def build_settlement_contract(web3: Any, settings: Settings) -> Any | None:
    """The contract to call ``settleTask`` on, or None when ``SETTLE_AFTER_PROOF`` is off."""
    if not settings.settle_after_proof:
        return None
    if not settings.settlement_address:
        raise ConfigurationError("SETTLE_AFTER_PROOF needs SETTLEMENT_ADDRESS")
    return web3.eth.contract(
        address=web3.to_checksum_address(settings.settlement_address),
        abi=load_contract_abi(settings.settlement_artifact),
    )

from pathlib import Path

from web3 import Web3

from common.config import Settings
from event_listener.config import load_contract_abi
from inference.pipeline import build_pipeline

ROOT = Path(__file__).resolve().parents[2]
TEAM_A_ARTIFACTS = ROOT / "team_a_assets"
TASK_MANAGER_ADDRESS = "0xA55f34e4137aD55f48aB268d4361F2225C4B5078"
ESCROW_ADDRESS = "0x5B685Ca03A8072FB3f9721Dd7017Ed0D8C719217"


def test_build_pipeline_wires_team_a_contracts_from_their_artifact_names() -> None:
    """The composed pipeline must load Team A's ABIs by their shipped artifact names."""
    settings = Settings(
        redis_url="redis://127.0.0.1:6379",
        web3_rpc_url="http://127.0.0.1:8545",
        task_manager_address=TASK_MANAGER_ADDRESS,
        escrow_address=ESCROW_ADDRESS,
        tee_mode="sgx",
    )

    pipeline = build_pipeline(settings)

    listener = pipeline.listener
    assert listener.task_manager.abi == load_contract_abi("TaskManager", TEAM_A_ARTIFACTS)
    assert listener.escrow.abi == load_contract_abi("EscrowContract", TEAM_A_ARTIFACTS)
    assert listener.escrow.address == Web3.to_checksum_address(ESCROW_ADDRESS)

"""Live NebulaGraph round-trip: write one contract and read it back (Req 10)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import settings
from backend.graph.client import NebulaGraphClient, ngql_str
from backend.legal_agent.models import Clause, ClauseType, ParsedContract

CONTRACT_ID = "verify-nebula"
TEXT = "Verification clause for the NebulaGraph round-trip."


def main() -> None:
    client = NebulaGraphClient(settings.nebula_host, settings.nebula_port)
    contract = ParsedContract(
        contract_id=CONTRACT_ID,
        clauses=[
            Clause(
                clause_type=ClauseType.TERMINATION,
                text=TEXT,
                start=0,
                end=len(TEXT),
                confidence=0.9,
            )
        ],
        entities=["Verification Corp"],
        jurisdiction="Verification",
    )
    client.add_contract(contract)
    rows = client.query(
        f'FETCH PROP ON contract "{ngql_str(CONTRACT_ID)}" YIELD properties(vertex);'
    )
    if not rows:
        raise SystemExit("round-trip failed: contract not found after insert")
    print(f"NebulaGraph round-trip successful at {settings.nebula_host}:{settings.nebula_port}.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: NebulaGraph verification failed: {exc}")
        raise SystemExit(1)

"""Live Weaviate round-trip: index one clause and search it back (Req 11)."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import settings
from backend.legal_agent.models import Clause, ClauseType, ParsedContract
from backend.search.engine import WeaviateSearchEngine

CONTRACT_ID = "verify-weaviate"
TEXT = "Live Weaviate round-trip: liability for consequential damages is limited."


def main() -> None:
    engine = WeaviateSearchEngine(url=settings.weaviate_url)
    contract = ParsedContract(
        contract_id=CONTRACT_ID,
        clauses=[
            Clause(
                clause_type=ClauseType.LIABILITY,
                text=TEXT,
                start=0,
                end=len(TEXT),
                confidence=0.99,
            )
        ],
        entities=["Verification Corp"],
        jurisdiction="Verification",
    )
    engine.index_clauses(contract, contract_date=date.today())
    results = engine.search(TEXT, clause_type="liability")
    if not any(r.contract_id == CONTRACT_ID for r in results.results):
        raise SystemExit(f"round-trip failed: {results.message or 'contract not found'}")
    print(f"Weaviate round-trip successful at {settings.weaviate_url}.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: Weaviate verification failed: {exc}")
        raise SystemExit(1)

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env", override=True)

sys.path.insert(0, str(REPO_ROOT))

from backend.legal_agent.extraction import LLMClauseExtractor
from backend.legal_agent.models import ClauseType

CONTRACT_TEXT = """MASTER SERVICES AGREEMENT

This Master Services Agreement is entered into between Acme Corp. ("Customer") and Globex Inc. ("Supplier").

1. PAYMENT TERMS. Customer shall pay all undisputed invoice amounts within thirty (30) days of receipt of invoice. Late payments shall accrue interest at 1.5% per month.

2. CONFIDENTIALITY. Each party agrees to maintain in strict confidence all proprietary information disclosed by the other party and not to disclose such information to any third party.

3. LIMITATION OF LIABILITY. In no event shall either party be liable for consequential, indirect, or punitive damages. Total aggregate liability under this agreement shall be capped at total fees paid in the preceding twelve months.

4. INDEMNIFICATION. Supplier agrees to defend, indemnify, and hold harmless Customer against any third-party claims arising from intellectual property infringement.

5. TERMINATION. Either party may terminate this agreement for cause upon thirty (30) days prior written notice if the other party breaches any material term.

6. DISPUTE RESOLUTION. Any dispute, claim, or controversy arising out of or relating to this agreement shall be submitted to binding arbitration in Delaware under AAA rules.

7. FORCE MAJEURE. Neither party shall be liable for any failure or delay in performance caused by acts of God, war, pandemic, or other events beyond reasonable control.
"""

def main():
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("GROQ_API_KEY"))
    if not has_key:
        print("ERROR: No LLM API key found in environment (.env).")
        print("Please configure ANTHROPIC_API_KEY or GROQ_API_KEY to run real model extraction.")
        sys.exit(1)

    print("Running LLMClauseExtractor with configured API key...")
    extractor = LLMClauseExtractor()
    clauses = extractor.extract(CONTRACT_TEXT)

    if not clauses:
        print("ERROR: LLM extraction returned 0 clauses or fell back.")
        sys.exit(1)

    found_types = set()
    for c in clauses:
        print(f"[{c.clause_type.value}] (conf: {c.confidence:.4f}) -> {c.text[:60]}...")
        assert 0.0 <= c.confidence <= 1.0, f"Confidence {c.confidence} out of range [0, 1]"
        found_types.add(c.clause_type)

    all_7_types = set(ClauseType)
    missing = all_7_types - found_types
    print(f"\nExtracted {len(clauses)} clauses covering {len(found_types)}/7 types.")
    if missing:
        print(f"Missing clause types: {[t.value for t in missing]}")
        sys.exit(1)

    print("SUCCESS: Real model output parsed into all 7 clause types with confidence in [0, 1] and zero fallbacks!")

if __name__ == "__main__":
    main()

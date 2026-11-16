"""Backend test suite — Properties 14-22 plus unit tests for tasks 7.x, 8.x, 10.x, 11.x, 12.x."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.graph.client import InMemoryGraph  # noqa: E402
from backend.legal_agent.agent import ContractAnalysisAgent  # noqa: E402
from backend.legal_agent.models import ClauseType, UnsupportedFormatError  # noqa: E402
from backend.rpc_wrapper import main as rpc_main  # noqa: E402
from backend.rpc_wrapper.chain import ChainError  # noqa: E402
from backend.search.engine import InMemorySearchEngine  # noqa: E402
from backend.storage.backends import InMemoryBackend, sha256_hex  # noqa: E402
from backend.storage.errors import IntegrityError  # noqa: E402
from backend.storage.onchain import ArtifactStore  # noqa: E402

CONTRACT_TEXT = """This Agreement is entered into between Acme Corp. and Globex Inc.

LIMITATION OF LIABILITY. Neither party shall be liable for consequential damages
arising out of this agreement, and total liability is capped at fees paid.

INDEMNIFICATION. Each party shall indemnify and hold harmless the other party.

CONFIDENTIALITY. Each party shall keep all proprietary information confidential
and shall not disclose confidential material to third parties.

TERMINATION. Either party may terminate this agreement for cause upon written
notice period of thirty days.

Payment shall be made net 30 upon invoice. Fees are payable within 30 days.

FORCE MAJEURE. Neither party is liable for delays caused by an act of god or
other events beyond the reasonable control of that party.

Any dispute shall be resolved by binding arbitration under the laws of Delaware,
and venue and jurisdiction shall lie in that state.
"""

SECOND_CONTRACT = """Master Services Agreement between Globex Inc. and Initech LLC.

CONFIDENTIALITY. The receiving party shall treat proprietary information as
confidential under this non-disclosure obligation.
"""


# --------------------------------------------------------------------------
# Storage — tasks 7.3 / 7.4, Property 14
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_property_14_storage_round_trip_with_integrity():
    """Property 14: upload then retrieve returns identical bytes."""
    backend = InMemoryBackend()
    recorded: list[tuple[str, str]] = []
    store = ArtifactStore(backend, lambda t, h: recorded.append((t, h)))

    for payload in [b"", b"model-weights", bytes(range(256)), b"x" * 100_000]:
        record = await store.store("task-1", payload)
        assert await store.load(record.content_hash) == payload
        assert record.content_hash == sha256_hex(payload)
        assert record.size_bytes == len(payload)

    assert len(recorded) == 4
    assert all(t == "task-1" for t, _ in recorded)


@pytest.mark.asyncio
async def test_upload_records_hash_on_chain():
    """Req 6.2: a successful upload triggers the on-chain recording call."""
    backend = InMemoryBackend()
    recorded = []
    store = ArtifactStore(backend, lambda t, h: recorded.append((t, h)))

    record = await store.store("task-42", b"artifact")
    assert recorded == [("task-42", record.content_hash)]


@pytest.mark.asyncio
async def test_tampered_data_raises_integrity_error():
    """Req 6.5: corrupted bytes are rejected rather than returned."""
    backend = InMemoryBackend()
    store = ArtifactStore(backend, lambda t, h: None)

    record = await store.store("task-1", b"original")
    backend.tamper(record.content_hash, b"corrupted")

    with pytest.raises(IntegrityError):
        await store.load(record.content_hash)


# --------------------------------------------------------------------------
# RPC wrapper — tasks 8.3 / 8.4, Properties 15-16
# --------------------------------------------------------------------------

class StubChain:
    def __init__(self, result=None, error: ChainError | None = None):
        self.result = result
        self.error = error
        self.calls: list[tuple] = []

    def is_connected(self):
        return True

    def read(self, contract, method, params):
        self.calls.append((contract, method, params))
        if self.error:
            raise self.error
        return self.result

    def submit(self, signed_tx):
        self.calls.append(("submit", signed_tx))
        if self.error:
            raise self.error
        return "0xdeadbeef"


@pytest.fixture
def client():
    c = TestClient(rpc_main.app)
    yield c
    rpc_main.set_client(None)


def test_property_15_read_response_format_and_schema_validation(client):
    """Property 15: valid requests return JSON; invalid schemas are rejected."""
    rpc_main.set_client(StubChain(result=12345))

    response = client.post(
        "/read",
        json={"contract": "EscrowContract", "method": "getBalance", "params": ["0xab"]},
    )
    assert response.status_code == 200
    assert response.json() == {
        "contract": "EscrowContract",
        "method": "getBalance",
        "result": 12345,
    }

    # Unknown contract, missing method, and wrong param type are all 422.
    for bad in [
        {"contract": "NotAContract", "method": "x"},
        {"contract": "EscrowContract"},
        {"contract": "EscrowContract", "method": "x", "params": "not-a-list"},
    ]:
        assert client.post("/read", json=bad).status_code == 422


def test_property_16_rpc_error_response_structure(client):
    """Property 16: a failed submission returns a code and a message."""
    rpc_main.set_client(StubChain(error=ChainError("TransactionRejected", "nonce too low")))

    response = client.post("/submit", json={"signed_tx": "0xf86b..."})
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "TransactionRejected"
    assert "nonce too low" in body["message"]


def test_submit_returns_tx_hash(client):
    rpc_main.set_client(StubChain())
    response = client.post("/submit", json={"signed_tx": "0xf86b..."})
    assert response.status_code == 200
    assert response.json()["tx_hash"] == "0xdeadbeef"


def test_bytes_results_are_json_safe(client):
    rpc_main.set_client(StubChain(result=b"\x01\x02"))
    response = client.post("/read", json={"contract": "TaskManager", "method": "getStatus"})
    assert response.json()["result"] == "0x0102"


# --------------------------------------------------------------------------
# Contract analysis agent — tasks 10.2 / 10.3, Property 17
# --------------------------------------------------------------------------

def test_property_17_confidence_scores_within_range():
    """Property 17: every clause confidence lies in [0.0, 1.0]."""
    parsed = ContractAnalysisAgent().analyze("c1", CONTRACT_TEXT, "txt")
    assert parsed.clauses
    for clause in parsed.clauses:
        assert 0.0 <= clause.confidence <= 1.0


def test_unsupported_format_lists_supported_formats():
    """Req 9.4."""
    with pytest.raises(UnsupportedFormatError) as exc:
        ContractAnalysisAgent().analyze("c1", b"data", "csv")
    assert exc.value.supported_formats == ["pdf", "docx", "txt"]


def test_clauses_carry_type_text_and_position():
    """Req 9.1: structured output with position metadata."""
    parsed = ContractAnalysisAgent().analyze("c1", CONTRACT_TEXT, "txt")
    for clause in parsed.clauses:
        assert isinstance(clause.clause_type, ClauseType)
        assert clause.text
        assert 0 <= clause.start < clause.end <= len(CONTRACT_TEXT)
        # Offsets must actually point at the clause in the source document.
        assert clause.text in CONTRACT_TEXT[clause.start : clause.end]


def test_all_seven_clause_types_are_identifiable():
    """Req 9.2: the full taxonomy is reachable."""
    parsed = ContractAnalysisAgent().analyze("c1", CONTRACT_TEXT, "txt")
    found = {c.clause_type for c in parsed.clauses}
    assert found == set(ClauseType), f"missing: {set(ClauseType) - found}"


# --------------------------------------------------------------------------
# Legal graph — tasks 11.2 / 11.3, Properties 18-19
# --------------------------------------------------------------------------

def test_property_18_graph_construction_completeness():
    """Property 18: node and edge counts match the parsed data."""
    graph = InMemoryGraph()
    parsed = ContractAnalysisAgent().analyze("c1", CONTRACT_TEXT, "txt")

    counts = graph.add_contract(parsed)
    assert counts["clauses"] == len(parsed.clauses)
    assert graph.clause_count("c1") == len(parsed.clauses)
    # 1 contract + one vertex per clause + one per entity.
    assert len(graph.state.vertices) == 1 + len(parsed.clauses) + len(parsed.entities)


def test_property_19_cross_contract_auto_linking():
    """Property 19: a shared legal entity creates edges between contracts."""
    graph = InMemoryGraph()
    agent = ContractAnalysisAgent()

    graph.add_contract(agent.analyze("c1", CONTRACT_TEXT, "txt"))
    graph.add_contract(agent.analyze("c2", SECOND_CONTRACT, "txt"))

    # Both name Globex Inc., so they must be linked in both directions.
    assert "c1" in graph.linked_contracts("c2")
    assert "c2" in graph.linked_contracts("c1")


def test_unrelated_contracts_are_not_linked():
    graph = InMemoryGraph()
    agent = ContractAnalysisAgent()
    graph.add_contract(agent.analyze("c1", CONTRACT_TEXT, "txt"))
    graph.add_contract(
        agent.analyze("c3", "Agreement with Unrelated Pvt Ltd.\n\nPayment net 30 invoice.", "txt")
    )
    assert graph.linked_contracts("c3") == set()


def test_rejects_unknown_edge_type():
    graph = InMemoryGraph()
    graph.state.add_vertex("a", "contract")
    graph.state.add_vertex("b", "contract")
    with pytest.raises(ValueError):
        graph.state.add_edge("a", "b", "not_a_real_edge")


# --------------------------------------------------------------------------
# Semantic search — tasks 12.2 / 12.3, Properties 20-22
# --------------------------------------------------------------------------

def indexed_engine(threshold: float = 0.1) -> InMemorySearchEngine:
    engine = InMemorySearchEngine(threshold=threshold)
    agent = ContractAnalysisAgent()
    engine.index_clauses(agent.analyze("c1", CONTRACT_TEXT, "txt"), contract_date=date(2024, 3, 1))
    engine.index_clauses(agent.analyze("c2", SECOND_CONTRACT, "txt"), contract_date=date(2025, 6, 1))
    return engine


def test_property_20_results_ordered_by_descending_relevance():
    """Property 20: descending order, scores within [0.0, 1.0]."""
    engine = indexed_engine()
    response = engine.search("confidential proprietary information disclosure")

    assert response.results
    scores = [r.relevance for r in response.results]
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 <= s <= 1.0 for s in scores)


def test_property_21_filters_are_respected():
    """Property 21: every returned result matches the applied filters."""
    engine = indexed_engine()

    by_type = engine.search("confidential information", clause_type="confidentiality")
    assert by_type.results
    assert all(r.clause_type == "confidentiality" for r in by_type.results)

    by_jurisdiction = engine.search("liability damages", jurisdiction="Delaware")
    assert all(r.jurisdiction == "Delaware" for r in by_jurisdiction.results)

    by_date = engine.search(
        "confidential information", date_range=(date(2025, 1, 1), date(2025, 12, 31))
    )
    assert all(r.contract_date.year == 2025 for r in by_date.results)


def test_property_22_below_threshold_returns_empty_with_message():
    """Property 22: nothing above threshold yields an empty set and an explanation."""
    engine = indexed_engine(threshold=0.5)
    response = engine.search("entirely unrelated quantum astrophysics telemetry")

    assert response.results == []
    assert response.message is not None
    assert "0.5" in response.message


def test_indexing_reports_clause_count():
    """Req 11.5."""
    engine = InMemorySearchEngine()
    parsed = ContractAnalysisAgent().analyze("c1", CONTRACT_TEXT, "txt")
    assert engine.index_clauses(parsed) == len(parsed.clauses)


# --------------------------------------------------------------------------
# Task 13.1 — agent wired to both stores
# --------------------------------------------------------------------------

def test_agent_stores_in_graph_and_search_index():
    """Req 9.5, 10.1, 11.5: one parse populates both stores."""
    graph = InMemoryGraph()
    engine = InMemorySearchEngine(threshold=0.1)
    agent = ContractAnalysisAgent(graph_client=graph, search_engine=engine)

    parsed = agent.analyze("c1", CONTRACT_TEXT, "txt")

    assert graph.clause_count("c1") == len(parsed.clauses)
    assert engine.search("liability damages").results

def test_pdf_extraction():
    import io
    from reportlab.pdfgen import canvas
    from backend.legal_agent.agent import ContractAnalysisAgent
    
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(100, 700, "CONFIDENTIALITY. This is a confidential document.")
    c.save()
    pdf_bytes = buf.getvalue()
    
    parsed = ContractAnalysisAgent().analyze("pdf_test", pdf_bytes, "pdf")
    assert parsed.clauses
    assert parsed.clauses[0].clause_type.value == "confidentiality"

def test_docx_extraction():
    import io
    import docx
    from backend.legal_agent.agent import ContractAnalysisAgent
    
    doc = docx.Document()
    doc.add_paragraph("TERMINATION. Either party may terminate.")
    
    buf = io.BytesIO()
    doc.save(buf)
    docx_bytes = buf.getvalue()
    
    parsed = ContractAnalysisAgent().analyze("docx_test", docx_bytes, "docx")
    assert parsed.clauses
    assert parsed.clauses[0].clause_type.value == "termination"

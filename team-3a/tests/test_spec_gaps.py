"""Tests for requirements that earlier tests did not actually exercise:
6.4/6.5 (IPFS timeout + CID integrity), 7.3 (2s read budget), 10.2 (depends_on /
conflicts_with / supersedes edges), 10.3-10.4 (paths and Cypher-style queries)."""
from __future__ import annotations

import hashlib
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.graph.client import EDGE_TYPES, InMemoryGraph  # noqa: E402
from backend.legal_agent.agent import ContractAnalysisAgent  # noqa: E402
from backend.legal_agent.extraction import parse_document  # noqa: E402
from backend.legal_agent.relations import detect_clause_relationships, detect_supersedes  # noqa: E402
from backend.legal_agent.models import Clause, ClauseType  # noqa: E402
from backend.rpc_wrapper.chain import ChainClient, ChainError  # noqa: E402
from backend.storage.backends import IPFSBackend  # noqa: E402
from backend.storage.errors import IntegrityError, StorageUnavailableError  # noqa: E402
from backend.storage.onchain import ArtifactStore  # noqa: E402


# --- IPFS: Req 6.4 timeout, Req 6.5 integrity with a real CID-style address ----------------

def fake_kubo(store: dict[str, bytes]) -> httpx.MockTransport:
    """Just enough of Kubo's HTTP API. Its 'CID' is deliberately NOT a plain sha256, like a
    real CID, so code that compares CIDs to sha256 digests fails against it."""

    def cid_of(data: bytes) -> str:
        return "Qm" + hashlib.sha256(b"unixfs:" + data).hexdigest()[:44]

    def file_bytes(request: httpx.Request) -> bytes:
        body = request.content
        start = body.index(b"\r\n\r\n") + 4
        end = body.rindex(b"\r\n--")
        return body[start:end]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v0/add":
            data = file_bytes(request)
            cid = cid_of(data)
            if request.url.params.get("only-hash") != "true":
                store[cid] = data
            return httpx.Response(200, json={"Hash": cid})
        if request.url.path == "/api/v0/cat":
            return httpx.Response(200, content=store[request.url.params["arg"]])
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_ipfs_round_trip_verifies_against_the_cid_not_sha256():
    store: dict[str, bytes] = {}
    backend = IPFSBackend(api_url="http://ipfs", transport=fake_kubo(store))
    artifacts = ArtifactStore(backend, lambda t, h: None)
    record = await artifacts.store("task", b"model weights")
    assert record.content_hash.startswith("Qm")
    assert await artifacts.load(record.content_hash) == b"model weights"


@pytest.mark.asyncio
async def test_ipfs_tampered_bytes_raise_integrity_error():
    store: dict[str, bytes] = {}
    backend = IPFSBackend(api_url="http://ipfs", transport=fake_kubo(store))
    artifacts = ArtifactStore(backend, lambda t, h: None)
    record = await artifacts.store("task", b"original")
    store[record.content_hash] = b"corrupted"
    with pytest.raises(IntegrityError):
        await artifacts.load(record.content_hash)


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [httpx.ReadTimeout("slow"), httpx.ConnectError("refused")])
async def test_ipfs_unreachable_or_slow_raises_storage_unavailable(exc):
    def boom(request):
        raise exc

    backend = IPFSBackend(api_url="http://ipfs", timeout=30, transport=httpx.MockTransport(boom))
    with pytest.raises(StorageUnavailableError) as info:
        await backend.upload(b"x")
    assert info.value.status_code == "StorageUnavailable"


def test_ipfs_client_uses_the_30_second_budget_by_default():
    assert IPFSBackend(api_url="http://ipfs").timeout == 30


# --- Req 7.3: reads are bounded -------------------------------------------------------------

def test_read_against_a_hung_node_fails_fast_with_read_timeout():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    conns = []
    threading.Thread(target=lambda: conns.append(srv.accept()), daemon=True).start()
    client = ChainClient(
        provider_url=f"http://127.0.0.1:{srv.getsockname()[1]}",
        addresses={"TaskManager": "0x" + "11" * 20},
        read_timeout=0.5,
    )
    started = time.monotonic()
    with pytest.raises(ChainError) as info:
        client.read("TaskManager", "taskCount", [])
    srv.close()
    assert info.value.code == "ReadTimeout"
    assert time.monotonic() - started < 2.0


# --- Req 10.2: depends_on / conflicts_with / supersedes ---------------------------------

def clause(ctype, text):
    return Clause(clause_type=ctype, text=text, start=0, end=len(text), confidence=0.9)


def test_depends_on_and_conflicts_with_between_clauses():
    clauses = [
        clause(ClauseType.PAYMENT_TERMS, "Fees are payable within 30 days, subject to the termination provisions below."),
        clause(ClauseType.TERMINATION, "Either party may terminate for cause."),
        clause(ClauseType.LIABILITY, "Notwithstanding any indemnification obligations, liability is capped."),
        clause(ClauseType.INDEMNIFICATION, "Each party shall indemnify the other."),
    ]
    found = set(detect_clause_relationships(clauses))
    assert (0, 1, "depends_on") in found
    assert (2, 3, "conflicts_with") in found


def test_no_edge_for_a_reference_to_a_clause_type_the_contract_lacks():
    clauses = [clause(ClauseType.PAYMENT_TERMS, "Fees are payable subject to the termination provisions.")]
    assert detect_clause_relationships(clauses) == []


def test_no_self_edges_and_no_edges_without_a_cue():
    clauses = [
        clause(ClauseType.LIABILITY, "Neither party shall be liable for damages."),
        clause(ClauseType.TERMINATION, "Either party may terminate this agreement."),
    ]
    assert detect_clause_relationships(clauses) == []


@pytest.mark.parametrize("text,expected", [
    ("This Agreement supersedes all prior agreements between the parties.", True),
    ("This agreement replaces the previous contract dated 2023.", True),
    ("The parties shall cooperate in good faith.", False),
])
def test_supersedes_detection(text, expected):
    assert detect_supersedes(text) is expected


V1 = "Master Agreement between Acme Corp. and Globex Inc.\n\nCONFIDENTIALITY. Keep proprietary information confidential.\n"
V2 = ("Amended Agreement between Acme Corp. and Globex Inc. This Agreement supersedes all prior agreements.\n\n"
      "PAYMENT. Fees are payable within 30 days, subject to the termination provisions.\n\n"
      "TERMINATION. Either party may terminate for cause.\n")
UNRELATED = "Services Agreement between Initech LLC. and Hooli Inc.\n\nCONFIDENTIALITY. Keep proprietary information confidential.\n"


def build_graph():
    graph = InMemoryGraph()
    agent = ContractAnalysisAgent(graph_client=graph, use_langgraph=False)
    for cid, text in (("v1", V1), ("other", UNRELATED), ("v2", V2)):
        agent.analyze(cid, text, "txt")
    return graph


def edge_set(graph, edge_type):
    return {(e.src, e.dst) for e in graph.state.edges if e.edge_type == edge_type}


def test_supersedes_edge_points_at_the_earlier_contract_sharing_parties():
    graph = build_graph()
    assert edge_set(graph, "supersedes") == {("v2", "v1")}  # not "other": no shared parties


def test_clause_edges_are_written_to_the_graph():
    graph = build_graph()
    assert ("v2::clause::0", "v2::clause::1") in edge_set(graph, "depends_on")


def test_every_edge_uses_a_declared_type():
    assert {e.edge_type for e in build_graph().state.edges} <= set(EDGE_TYPES)


def test_relationships_are_empty_not_missing_for_plain_documents():
    parsed = parse_document("p", UNRELATED, "txt")
    assert parsed.relationships == [] and parsed.supersedes_prior is False


# --- Req 10.3 / 10.4: paths and Cypher-style queries --------------------------------------------

def test_find_paths_between_contracts_through_shared_references():
    graph = build_graph()
    paths = graph.find_paths("v2", "v1", max_depth=2, edge_types=("references", "supersedes"))
    assert ["v2", "v1"] in paths


def test_cypher_match_returns_edges_of_the_requested_type():
    rows = build_graph().query('MATCH (a)-[:supersedes]->(b) RETURN id(a), id(b)')
    assert rows == [{"id(a)": "v2", "id(b)": "v1"}]


def test_cypher_where_and_tag_filters():
    graph = build_graph()
    rows = graph.query('MATCH (c:contract)-[:contains]->(k:clause) WHERE id(c) == "v2" RETURN id(k)')
    assert sorted(r["id(k)"] for r in rows) == ["v2::clause::0", "v2::clause::1"]


@pytest.mark.parametrize("bad", [
    "DROP SPACE legal",
    "MATCH (a)-[:made_up]->(b) RETURN id(a)",
    "MATCH (a)-[:contains]->(b) RETURN a.secret",
])
def test_unsupported_queries_raise_instead_of_returning_nothing(bad):
    with pytest.raises(ValueError):
        build_graph().query(bad)


def test_service_exposes_graph_query_and_paths(monkeypatch):
    from backend.legal_agent import service

    g = build_graph()
    monkeypatch.setattr(service, "graph", g)
    c = TestClient(service.app)
    r = c.post("/graph/query", json={"statement": "MATCH (a)-[:supersedes]->(b) RETURN id(a), id(b)"})
    assert r.json()["rows"] == [{"id(a)": "v2", "id(b)": "v1"}]
    assert c.post("/graph/query", json={"statement": "DROP SPACE x"}).status_code == 400
    assert c.get("/graph/paths", params={"src": "v2", "dst": "v1", "max_depth": 2}).status_code == 200
    assert c.get("/graph/paths", params={"src": "a", "dst": "b", "max_depth": 99}).status_code == 422


def test_service_indexes_contract_date_and_filters_search_by_range(monkeypatch):
    from backend.legal_agent import service
    from backend.search.engine import InMemorySearchEngine

    engine = InMemorySearchEngine(threshold=0.1)
    monkeypatch.setattr(service, "search", engine)
    monkeypatch.setattr(
        service,
        "agent",
        ContractAnalysisAgent(graph_client=InMemoryGraph(), search_engine=engine, use_langgraph=False),
    )
    c = TestClient(service.app)
    text = "Neither party shall be liable for consequential damages."
    for contract_id, contract_date in (("old", "2024-05-01"), ("new", "2025-05-01")):
        r = c.post("/analyze", json={
            "contract_id": contract_id, "text": text, "format": "txt", "contract_date": contract_date,
        })
        assert r.status_code == 200

    everything = c.post("/search", json={"query": text, "limit": 10}).json()["results"]
    assert {h["contract_id"] for h in everything} == {"old", "new"}

    in_2024 = c.post("/search", json={
        "query": text, "date_range": ["2024-01-01", "2024-12-31"],
    }).json()["results"]
    assert {h["contract_id"] for h in in_2024} == {"old"}

    inverted = c.post("/search", json={"query": text, "date_range": ["2025-01-01", "2024-01-01"]})
    assert inverted.status_code == 422


def test_uploaded_file_can_carry_a_contract_date(monkeypatch):
    from backend.legal_agent import service
    from backend.search.engine import InMemorySearchEngine

    engine = InMemorySearchEngine(threshold=0.1)
    monkeypatch.setattr(service, "search", engine)
    monkeypatch.setattr(
        service,
        "agent",
        ContractAnalysisAgent(graph_client=InMemoryGraph(), search_engine=engine, use_langgraph=False),
    )
    c = TestClient(service.app)
    text = "Neither party shall be liable for consequential damages."
    r = c.post(
        "/analyze-file",
        data={"contract_id": "uploaded", "contract_date": "2023-03-01"},
        files={"file": ("contract.txt", text, "text/plain")},
    )
    assert r.status_code == 200

    hits = c.post("/search", json={
        "query": text, "date_range": ["2023-01-01", "2023-12-31"],
    }).json()["results"]
    assert {h["contract_id"] for h in hits} == {"uploaded"}
    miss = c.post("/search", json={
        "query": text, "date_range": ["2024-01-01", "2024-12-31"],
    }).json()["results"]
    assert miss == []


def test_cypher_multi_hop_query():
    graph = build_graph()
    rows = graph.query('MATCH (c:contract)-[:contains]->(k1:clause)-[:depends_on]->(k2:clause) RETURN id(c), id(k1), id(k2)')
    assert len(rows) == 1
    assert rows[0] == {
        "id(c)": "v2",
        "id(k1)": "v2::clause::0",
        "id(k2)": "v2::clause::1",
    }


def test_cypher_path_traversal_variable_length():
    graph = build_graph()
    rows = graph.query('MATCH p = (a)-[:references*1..2]->(b) WHERE id(a) == "v2" RETURN p')
    assert any(r["p"] == ["v2", "v1"] for r in rows)


def test_all_six_edge_types_created_and_queryable():
    # Build a graph with clauses that produce all six edge types
    g = InMemoryGraph()
    # 1. contains & party_to
    from backend.legal_agent.models import Clause, ClauseType, ParsedContract
    c1 = ParsedContract(
        contract_id="c1",
        clauses=[
            Clause(clause_type=ClauseType.TERMINATION, text="May terminate.", start=0, end=14, confidence=0.9),
            Clause(clause_type=ClauseType.PAYMENT_TERMS, text="Payment subject to termination.", start=15, end=46, confidence=0.9),
            Clause(clause_type=ClauseType.LIABILITY, text="Notwithstanding payment, liability capped.", start=47, end=89, confidence=0.9),
        ],
        entities=["Alpha Corp", "Beta LLC"],
        jurisdiction="Delaware",
        relationships=[(1, 0, "depends_on"), (2, 1, "conflicts_with")],
        supersedes_prior=False,
    )
    c2 = ParsedContract(
        contract_id="c2",
        clauses=[
            Clause(clause_type=ClauseType.CONFIDENTIALITY, text="Confidentiality applies.", start=0, end=24, confidence=0.9),
        ],
        entities=["Alpha Corp"],
        jurisdiction="Delaware",
        relationships=[],
        supersedes_prior=True,
    )
    g.add_contract(c1)
    g.add_contract(c2)

    # Query each of the six edge types via Cypher
    r_contains = g.query('MATCH (c)-[:contains]->(k) WHERE id(c) == "c1" RETURN id(k)')
    assert len(r_contains) == 3

    r_party_to = g.query('MATCH (c)-[:party_to]->(e) WHERE id(c) == "c1" RETURN id(e)')
    assert len(r_party_to) == 2

    r_depends = g.query('MATCH (k1)-[:depends_on]->(k2) RETURN id(k1), id(k2)')
    assert len(r_depends) == 1
    assert r_depends[0] == {"id(k1)": "c1::clause::1", "id(k2)": "c1::clause::0"}

    r_conflicts = g.query('MATCH (k1)-[:conflicts_with]->(k2) RETURN id(k1), id(k2)')
    assert len(r_conflicts) == 1
    assert r_conflicts[0] == {"id(k1)": "c1::clause::2", "id(k2)": "c1::clause::1"}

    r_references = g.query('MATCH (a)-[:references]->(b) WHERE id(a) == "c2" RETURN id(b)')
    assert any(row["id(b)"] == "c1" for row in r_references)

    r_supersedes = g.query('MATCH (a)-[:supersedes]->(b) WHERE id(a) == "c2" RETURN id(b)')
    assert len(r_supersedes) == 1
    assert r_supersedes[0]["id(b)"] == "c1"

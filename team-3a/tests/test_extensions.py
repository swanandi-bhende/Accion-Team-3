"""Tests for the work that closed the open items: document decoding, the LLM extractor,
nGQL escaping, and the RPC wrapper's results / upload endpoints."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.graph.client import ngql_str  # noqa: E402
from backend.legal_agent import agent as agent_mod  # noqa: E402
from backend.legal_agent.agent import ContractAnalysisAgent  # noqa: E402
from backend.legal_agent.extraction import LLMClauseExtractor, extract_text  # noqa: E402
from backend.legal_agent.models import ClauseType, DocumentDecodeError  # noqa: E402
from backend.rpc_wrapper import main as rpc_main  # noqa: E402
from backend.rpc_wrapper.chain import ChainClient, ChainError  # noqa: E402
from backend.storage.backends import InMemoryBackend  # noqa: E402

TEXT = (
    "LIMITATION OF LIABILITY. Neither party shall be liable for consequential damages.\n\n"
    "CONFIDENTIALITY. Each party shall keep proprietary information confidential.\n"
)


def make_docx(paragraphs: list[str]) -> bytes:
    import docx

    d = docx.Document()
    for p in paragraphs:
        d.add_paragraph(p)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def make_pdf(text: str) -> bytes:
    """Smallest valid single-page PDF carrying `text`."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return out


# --- document decoding -------------------------------------------------------

def test_docx_is_decoded_and_paragraphs_become_clause_boundaries():
    data = make_docx(["LIMITATION OF LIABILITY. Neither party is liable for damages.",
                      "CONFIDENTIALITY. Keep proprietary information confidential."])
    text = extract_text(data, "docx")
    assert "liable for damages" in text
    parsed = ContractAnalysisAgent(use_langgraph=False).analyze("c-docx", data, "docx")
    assert {c.clause_type for c in parsed.clauses} >= {ClauseType.LIABILITY, ClauseType.CONFIDENTIALITY}


def test_pdf_is_decoded():
    text = extract_text(make_pdf("Neither party shall be liable for damages"), "pdf")
    assert "liable for damages" in text


@pytest.mark.parametrize("fmt", ["pdf", "docx"])
def test_corrupt_binary_raises_document_decode_error(fmt):
    with pytest.raises(DocumentDecodeError):
        extract_text(b"this is not a real document", fmt)


# --- LLM extractor (fake clients; no network) -------------------------------------

class FakeAnthropic:
    def __init__(self, payload: str):
        self.messages = SimpleNamespace(create=lambda **kw: SimpleNamespace(
            content=[SimpleNamespace(text=payload)]))


class FakeGroq:
    def __init__(self, payload: str):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=payload))])))


def test_llm_extractor_rederives_offsets_and_clamps_confidence():
    payload = json.dumps({"clauses": [
        {"clause_type": "liability", "text": "Neither party shall be liable for consequential damages.",
         "confidence": 7.5, "start": 9999, "end": 99999},
        {"clause_type": "confidentiality", "text": "Each party shall keep proprietary information confidential.",
         "confidence": -2},
    ]})
    clauses = LLMClauseExtractor(client=FakeAnthropic(payload), provider="anthropic").extract(TEXT)
    assert [c.clause_type for c in clauses] == [ClauseType.LIABILITY, ClauseType.CONFIDENTIALITY]
    assert all(0.0 <= c.confidence <= 1.0 for c in clauses)  # Property 17
    for c in clauses:
        assert TEXT[c.start:c.end] == c.text  # offsets come from the document, not the model


def test_llm_extractor_drops_paraphrases_and_unknown_types_and_strips_fences():
    payload = "```json\n" + json.dumps([
        {"clause_type": "liability", "text": "a paraphrase that is not in the document", "confidence": 0.9},
        {"clause_type": "made_up_type", "text": "Neither party shall be liable", "confidence": 0.9},
        {"clause_type": "liability", "text": "Neither party shall be liable", "confidence": 0.8},
    ]) + "\n```"
    clauses = LLMClauseExtractor(client=FakeGroq(payload), provider="groq").extract(TEXT)
    assert len(clauses) == 1 and clauses[0].confidence == 0.8


def test_llm_extractor_returns_empty_on_garbage():
    assert LLMClauseExtractor(client=FakeAnthropic("not json at all"), provider="anthropic").extract(TEXT) == []


def test_agent_falls_back_to_rules_when_llm_yields_nothing(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("USE_LLM_EXTRACTOR", "true")
    monkeypatch.setattr(agent_mod, "LLMClauseExtractor", lambda: LLMClauseExtractor(
        client=FakeAnthropic("garbage"), provider="anthropic"))
    parsed = ContractAnalysisAgent(use_langgraph=False).analyze("c", TEXT, "txt")
    assert parsed.clauses  # rule-based result


def test_api_keys_are_only_sent_to_their_own_provider(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    # Anthropic key present -> provider must be anthropic, never groq.
    assert LLMClauseExtractor().provider == "anthropic"


# --- nGQL escaping ------------------------------------------------------------

def test_ngql_str_blocks_string_breakout():
    evil = 'x"); DROP SPACE legal; //'
    out = ngql_str(evil)
    assert '"' not in out.replace('\\"', "")  # every quote is escaped
    assert ngql_str("a\\") == "a\\\\"          # trailing backslash cannot eat the closing quote
    assert "\n" not in ngql_str("a\nb")


# --- RPC wrapper: /config, /tasks, /upload ---------------------------------------------

TASK_ID = "0x" + "ab" * 32


class ViewChain:
    def is_connected(self):
        return True

    def network_config(self):
        return {"chain_id": 11155111, "addresses": {"TaskManager": "0x1"}, "events": {}}

    def task_view(self, task_id):
        if task_id != TASK_ID:
            raise ChainError("TaskNotFound", "no such task")
        return {"task_id": task_id, "status": "Completed", "proof": {"model_cid": "bafy"}}

    def build_call(self, contract, method, params):
        return {"to": "0xTaskManager", "data": f"0xencoded:{method}:{params[1]}"}


@pytest.fixture
def rpc():
    rpc_main.set_client(ViewChain())
    rpc_main.set_storage(InMemoryBackend())
    yield TestClient(rpc_main.app)
    rpc_main.set_client(None)
    rpc_main.set_storage(None)


def test_config_endpoint(rpc):
    r = rpc.get("/config")
    assert r.status_code == 200 and r.json()["chain_id"] == 11155111


def test_task_endpoint_found_missing_and_malformed(rpc):
    assert rpc.get(f"/tasks/{TASK_ID}").json()["status"] == "Completed"
    assert rpc.get("/tasks/0x" + "cd" * 32).status_code == 404
    bad = rpc.get("/tasks/not-a-task-id")
    assert bad.status_code == 422 and bad.json()["code"] == "InvalidTaskId"


def test_upload_returns_hash_and_unsigned_recording_tx(rpc):
    r = rpc.post("/upload", data={"task_id": TASK_ID}, files={"file": ("m.bin", b"model-bytes")})
    body = r.json()
    assert r.status_code == 200
    assert body["record_tx"]["to"] == "0xTaskManager"
    assert body["content_hash"] in body["record_tx"]["data"]  # the call carries the uploaded hash
    assert body["size_bytes"] == len(b"model-bytes")


def test_upload_rejects_a_malformed_task_id(rpc):
    r = rpc.post("/upload", data={"task_id": "task-1"}, files={"file": ("m.bin", b"x")})
    assert r.status_code == 422


# --- real ABI encoding, offline ----------------------------------------------------------

def test_build_call_encodes_against_the_exported_abi():
    client = ChainClient(addresses={"TaskManager": "0x" + "11" * 20})
    call = client.build_call("TaskManager", "recordArtifact", [TASK_ID, "QmTest"])
    assert call["to"].lower() == "0x" + "11" * 20
    assert call["data"].startswith("0x") and TASK_ID[2:] in call["data"]


def test_exported_abi_contains_the_team_3b_interface():
    abi = json.loads((Path(__file__).resolve().parent.parent / "exports" / "TaskManager.abi.json").read_text())
    fn = next(i for i in abi if i.get("name") == "submitProofs")
    assert [x["type"] for x in fn["inputs"]] == ["bytes32[]", "bytes32[]", "string[]", "bytes32[]"]
    assert fn["outputs"][0]["type"] == "uint256"
    names = {i["name"] for i in abi if i["type"] == "event"}
    assert {"TaskCreated", "ProofSubmitted", "ProofRejected", "BidPlaced", "BidAccepted", "ArtifactRecorded"} <= names


def test_submit_returns_a_0x_prefixed_hash():
    from backend.rpc_wrapper.chain import _hex0x

    assert _hex0x(bytes.fromhex("ab" * 32)) == "0x" + "ab" * 32
    assert _hex0x(b"\x01") == "0x01"

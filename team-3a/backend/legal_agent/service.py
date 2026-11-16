"""HTTP entrypoint for the contract analysis agent (Requirements 9-11).

Runs the parse -> graph -> index pipeline behind a small API so the agent is
reachable as its own service in docker-compose.
"""
from __future__ import annotations

import os
from datetime import date

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field, model_validator

from backend.graph.client import InMemoryGraph, NebulaGraphClient
from backend.legal_agent.agent import ContractAnalysisAgent
from backend.legal_agent.models import DocumentDecodeError, UnsupportedFormatError
from backend.search.engine import InMemorySearchEngine, WeaviateSearchEngine

app = FastAPI(title="Team 3a Contract Analysis Agent", version="0.1.0")

def build_stores(live: bool):
    """USE_LIVE_BACKENDS=true selects NebulaGraph + Weaviate (docker-compose sets it);
    otherwise the in-memory reference implementations, which share their semantics."""
    if not live:
        return InMemoryGraph(), InMemorySearchEngine()
    graph_client = NebulaGraphClient()
    graph_client.connect()
    search_engine = WeaviateSearchEngine()
    search_engine.connect()
    return graph_client, search_engine


USE_LIVE = os.getenv("USE_LIVE_BACKENDS", "false").lower() in ("1", "true", "yes")
graph, search = build_stores(USE_LIVE)
agent = ContractAnalysisAgent(graph_client=graph, search_engine=search)


class AnalyzeRequest(BaseModel):
    contract_id: str = Field(min_length=1)
    text: str
    format: str = "txt"
    contract_date: date | None = None


class SearchRequest(BaseModel):
    query: str
    clause_type: str | None = None
    date_range: tuple[date, date] | None = None
    jurisdiction: str | None = None
    limit: int = 10

    @model_validator(mode="after")
    def check_date_range(self):
        if self.date_range and self.date_range[0] > self.date_range[1]:
            raise ValueError("date_range start must not be after end")
        return self


@app.get("/health")
def health() -> dict[str, object]:
    return {"status": "ok", "live_backends": USE_LIVE}


@app.post("/analyze")
def analyze(request: AnalyzeRequest) -> dict:
    try:
        parsed = agent.analyze(
            request.contract_id, request.text, request.format, contract_date=request.contract_date
        )
    except UnsupportedFormatError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "UnsupportedFormat", "message": str(exc)},
        )
    return {
        "contract_id": parsed.contract_id,
        "jurisdiction": parsed.jurisdiction,
        "entities": parsed.entities,
        "clauses": [c.to_dict() for c in parsed.clauses],
        "relationships": [
            {"source": s, "target": t, "type": ty} for s, t, ty in parsed.relationships
        ],
        "supersedes_prior": parsed.supersedes_prior,
    }


@app.post("/analyze-file")
async def analyze_file(
    contract_id: str = Form(...),
    file: UploadFile = File(...),
    contract_date: date | None = Form(None),
) -> dict:
    """Upload a PDF, DOCX or TXT contract; the format comes from the file extension."""
    fmt = (file.filename or "").rsplit(".", 1)[-1] if "." in (file.filename or "") else ""
    data = await file.read()
    try:
        parsed = agent.analyze(contract_id, data, fmt, contract_date=contract_date)
    except UnsupportedFormatError as exc:
        raise HTTPException(status_code=400, detail={"code": "UnsupportedFormat", "message": str(exc)})
    except DocumentDecodeError as exc:
        raise HTTPException(status_code=422, detail={"code": "DocumentDecodeError", "message": str(exc)})
    return {
        "contract_id": parsed.contract_id,
        "jurisdiction": parsed.jurisdiction,
        "entities": parsed.entities,
        "clauses": [c.to_dict() for c in parsed.clauses],
        "relationships": [
            {"source": s, "target": t, "type": ty} for s, t, ty in parsed.relationships
        ],
        "supersedes_prior": parsed.supersedes_prior,
    }


class GraphQuery(BaseModel):
    statement: str = Field(min_length=1)


@app.post("/graph/query")
def graph_query(request: GraphQuery) -> dict:
    """Cypher-style relationship query (Req 10.4)."""
    try:
        return {"rows": graph.query(request.statement)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "UnsupportedQuery", "message": str(exc)})


@app.get("/graph/paths")
def graph_paths(src: str, dst: str, max_depth: int = 4) -> dict:
    """All simple paths between two vertices (Req 10.3)."""
    if not 1 <= max_depth <= 8:
        raise HTTPException(status_code=422, detail={"code": "InvalidDepth", "message": "max_depth must be 1-8"})
    return {"paths": graph.find_paths(src, dst, max_depth=max_depth)}


@app.post("/search")
def search_clauses(request: SearchRequest) -> dict:
    response = search.search(
        request.query,
        clause_type=request.clause_type,
        date_range=request.date_range,
        jurisdiction=request.jurisdiction,
        limit=request.limit,
    )
    return {
        "results": [
            {
                "contract_id": r.contract_id,
                "clause_type": r.clause_type,
                "text": r.text,
                "relevance": r.relevance,
            }
            for r in response.results
        ],
        "message": response.message,
    }


if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.getenv("AGENT_HOST", "0.0.0.0"),
        port=int(os.getenv("AGENT_PORT", "8100")),
    )

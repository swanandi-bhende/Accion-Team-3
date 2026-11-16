"""FastAPI service exposing Web3 RPC endpoints (Requirement 7)."""
from __future__ import annotations

import re

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse

from backend.rpc_wrapper.chain import ChainClient, ChainError, to_jsonable
from backend.rpc_wrapper.models import (
    ReadRequest,
    ReadResponse,
    TransactionRequest,
    TransactionResponse,
)
from backend.storage.backends import IPFSBackend
from backend.storage.onchain import ArtifactStore

app = FastAPI(
    title="Team 3a RPC Wrapper",
    description="Web3 RPC endpoints for the escrow, identity, settlement and staking contracts.",
    version="0.1.0",
)

TASK_ID_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")

# Storage backend seam, same pattern as the chain client.
_storage = None


def get_storage():
    global _storage
    if _storage is None:
        _storage = IPFSBackend()
    return _storage


def set_storage(backend) -> None:
    """Test seam — injects an in-memory storage backend."""
    global _storage
    _storage = backend


# Module-level so tests can swap in a stub client via dependency override or assignment.
_client: ChainClient | None = None


def get_client() -> ChainClient:
    global _client
    if _client is None:
        _client = ChainClient()
    return _client


def set_client(client: ChainClient | None) -> None:
    """Test seam — injects a stub chain client."""
    global _client
    _client = client


@app.get("/health")
def health() -> dict[str, object]:
    return {"status": "ok", "chain_connected": get_client().is_connected()}


@app.post("/read", response_model=ReadResponse)
def read_state(request: ReadRequest) -> ReadResponse:
    """Reads contract state (Req 7.1, 7.3).

    Pydantic rejects an unknown contract name with a 422 before this body runs,
    which is the schema validation Req 7.6 asks for.
    """
    try:
        result = get_client().read(request.contract, request.method, request.params)
    except ChainError as exc:
        status = 504 if exc.code == "ReadTimeout" else 400
        raise HTTPException(status_code=status, detail={"code": exc.code, "message": exc.message})
    return ReadResponse(
        contract=request.contract, method=request.method, result=to_jsonable(result)
    )


@app.post("/submit", response_model=TransactionResponse)
def submit_transaction(request: TransactionRequest) -> TransactionResponse:
    """Relays a signed transaction to the network (Req 7.2, 7.4, 7.5)."""
    try:
        tx_hash = get_client().submit(request.signed_tx)
    except ChainError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message})
    return TransactionResponse(tx_hash=tx_hash)

@app.get("/config")
def network_config() -> dict:
    """Chain ID, deployed addresses and event sources — what a consumer needs to connect."""
    try:
        return get_client().network_config()
    except (ChainError, FileNotFoundError) as exc:
        raise HTTPException(status_code=503, detail={"code": "NotConfigured", "message": str(exc)})


@app.get("/tasks/{task_id}")
def task_summary(task_id: str) -> dict:
    """Task record, funding, worker, proof and artifact hash in one read (results access)."""
    if not TASK_ID_RE.match(task_id):
        raise HTTPException(
            status_code=422,
            detail={"code": "InvalidTaskId", "message": "task_id must be 0x + 64 hex chars (bytes32)"},
        )
    try:
        return get_client().task_view(task_id)
    except ChainError as exc:
        status = {"TaskNotFound": 404, "ReadTimeout": 504}.get(exc.code, 400)
        raise HTTPException(status_code=status, detail={"code": exc.code, "message": exc.message})


@app.post("/upload")
async def upload_artifact(task_id: str = Form(...), file: UploadFile = File(...)):
    """Uploads an artifact to IPFS (Req 6.1) and returns the unsigned on-chain recording
    call (Req 6.2).

    This service never holds keys, so it cannot send the recording transaction itself.
    The caller signs `record_tx` with their wallet and posts it to /submit.
    """
    if not TASK_ID_RE.match(task_id):
        raise HTTPException(
            status_code=422,
            detail={"code": "InvalidTaskId", "message": "task_id must be 0x + 64 hex chars (bytes32)"},
        )
    data = await file.read()
    recorded: list[tuple[str, str]] = []
    store = ArtifactStore(backend=get_storage(), recorder=lambda t, h: recorded.append((t, h)))
    try:
        record = await store.store(task_id, data)
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"code": "StorageUnavailable", "message": str(exc)})
    try:
        record_tx = get_client().build_call("TaskManager", "recordArtifact", [task_id, record.content_hash])
    except (ChainError, FileNotFoundError) as exc:
        # Upload succeeded; report the hash so the caller can still record it themselves.
        return {
            "task_id": record.task_id,
            "content_hash": record.content_hash,
            "size_bytes": record.size_bytes,
            "record_tx": None,
            "warning": f"on-chain recording call unavailable: {exc}",
        }
    return {
        "task_id": record.task_id,
        "content_hash": record.content_hash,
        "size_bytes": record.size_bytes,
        "record_tx": record_tx,
    }


@app.exception_handler(HTTPException)
def http_exception_handler(request, exc: HTTPException):
    """Normalises every error into {"code", "message"} (Req 7.5)."""
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail:
        return JSONResponse(status_code=exc.status_code, content=detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": f"HTTP{exc.status_code}", "message": str(detail)},
    )

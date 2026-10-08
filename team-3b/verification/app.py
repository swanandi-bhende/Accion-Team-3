"""FastAPI application for external proof verification."""

import asyncio
import json
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from common.exceptions import UnsupportedFormatError
from common.models import VerificationRequest
from verification.interface import VerificationInterface


def load_trusted_keys(path: str | None) -> dict[str, bytes]:
    """Load a ``signer_id -> Ed25519 public key`` map from a JSON file.

    Missing or unconfigured path means "trust nobody yet": the endpoint still
    verifies zkML proofs and rejects unverifiable attestations.
    """
    if not path or not Path(path).exists():
        return {}
    return {
        signer_id: bytes.fromhex(public_key)
        for signer_id, public_key in json.loads(Path(path).read_text(encoding="utf-8")).items()
    }


def create_app(interface: VerificationInterface, timeout_s: float = 5.0) -> FastAPI:
    app = FastAPI(title="Verifiable AI Verification Interface")

    @app.exception_handler(UnsupportedFormatError)
    async def unsupported_format_handler(_request: Request, error: UnsupportedFormatError):
        return JSONResponse(status_code=400, content={"error": str(error), "supported": error.supported})

    @app.post("/verify")
    async def verify(request: VerificationRequest):
        try:
            return await asyncio.wait_for(interface.verify(request), timeout=timeout_s)
        except TimeoutError:
            return JSONResponse(
                status_code=408,
                content={"result": False, "reason_code": "verification_timeout", "timestamp": time.time()},
            )

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


def create_default_app(timeout_s: float = 5.0) -> FastAPI:
    """Build the standalone verification service from the environment.

    Serve it with::

        uvicorn "verification.app:create_default_app" --factory --port 8001

    Only two optional variables are read, so the verifier does not depend on the
    blockchain/proof-submitter configuration: ``MODEL_STORAGE_URL`` (storage node)
    and ``TEE_TRUSTED_KEYS_PATH`` (a JSON map of ``signer_id`` to hex-encoded
    Ed25519 public key). With no trusted keys configured the endpoint still serves
    and verifies zkML proofs, and rejects unverifiable TEE attestations as
    ``invalid_signature``.
    """
    from common.config import get_settings
    from common.storage.ipfs_backend import IPFSBackend
    from verification.resolver import StorageProofResolver
    from verification.verifiers import ConcreteTEEVerifier, ConcreteZkMLVerifier

    settings = get_settings()
    storage = IPFSBackend(node_url=settings.model_storage_url or "http://localhost:5001")
    interface = VerificationInterface(
        ConcreteTEEVerifier(trusted_public_keys=load_trusted_keys(settings.tee_trusted_keys_path)),
        ConcreteZkMLVerifier(),
        StorageProofResolver(storage=storage),
    )
    return create_app(interface, timeout_s=timeout_s)

"""FastAPI application for external proof verification."""

import asyncio
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from common.exceptions import UnsupportedFormatError
from common.models import VerificationRequest
from verification.interface import VerificationInterface


def create_app(interface: VerificationInterface, timeout_s: float = 5.0) -> FastAPI:
    app = FastAPI(title="Verifiable AI Verification Interface")

    @app.exception_handler(UnsupportedFormatError)
    async def unsupported_format_handler(_request: Request, error: UnsupportedFormatError):
        return JSONResponse(status_code=400, content={"error": str(error), "supported": error.supported})

    @app.post("/verify")
    async def verify(request: VerificationRequest):
        try:
            return await asyncio.wait_for(interface.verify(request), timeout=timeout_s)
        except asyncio.TimeoutError:
            return JSONResponse(
                status_code=408,
                content={"result": False, "reason_code": "verification_timeout", "timestamp": time.time()},
            )

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app
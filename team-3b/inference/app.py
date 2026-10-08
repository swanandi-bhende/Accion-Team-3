from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from common.config import get_settings
from common.exceptions import (
    IntegrityCheckFailedError,
    ModelNotFoundError,
    TEEUnavailableError,
    TimeoutExceededError,
)
from common.model_registry import ModelRegistry
from common.models import ExecutionReceipt, InferenceRequest
from common.storage.ipfs_backend import IPFSBackend
from inference.engine import InferenceEngine
from inference.tee_runtime import TEERuntime


def build_app_state(app: FastAPI):
    if hasattr(app.state, "inference_engine"):
        return app.state

    settings = get_settings().require("redis_url", "tee_mode", "model_storage_url")

    redis_client = redis.Redis.from_url(settings.redis_url)
    storage_backend = IPFSBackend(node_url=settings.model_storage_url)
    model_registry = ModelRegistry(redis_client, storage_backend)

    tee_runtime = TEERuntime(settings.tee_mode)
    tee_runtime.initialize()

    inference_engine = InferenceEngine(
        registry=model_registry,
        tee=tee_runtime,
        redis_client=redis_client,
        timeout_s=settings.tee_inference_timeout_s
    )

    app.state.redis_client = redis_client
    app.state.storage_backend = storage_backend
    app.state.model_registry = model_registry
    app.state.tee_runtime = tee_runtime
    app.state.inference_engine = inference_engine
    return app.state


@asynccontextmanager
async def lifespan(app: FastAPI):
    state = build_app_state(app)
    yield
    tee_runtime = getattr(state, "tee_runtime", None)
    redis_client = getattr(state, "redis_client", None)
    if tee_runtime is not None:
        tee_runtime.shutdown()
    if redis_client is not None:
        await redis_client.close()

app = FastAPI(title="Inference Service", lifespan=lifespan)

# Dependency Injector
def get_inference_engine(request: Request) -> InferenceEngine:
    if not hasattr(request.app.state, "inference_engine"):
        build_app_state(request.app)
    return request.app.state.inference_engine

# Exception Handlers
@app.exception_handler(ModelNotFoundError)
async def model_not_found_handler(request: Request, exc: ModelNotFoundError):
    return JSONResponse(status_code=404, content={"error": "ModelNotFound", "detail": str(exc)})

@app.exception_handler(TimeoutExceededError)
async def timeout_handler(request: Request, exc: TimeoutExceededError):
    return JSONResponse(status_code=408, content={"error": "TimeoutExceeded", "detail": str(exc)})

@app.exception_handler(TEEUnavailableError)
async def tee_unavailable_handler(request: Request, exc: TEEUnavailableError):
    return JSONResponse(status_code=503, content={"error": "TEEUnavailable", "detail": str(exc)})

@app.exception_handler(IntegrityCheckFailedError)
async def integrity_check_handler(request: Request, exc: IntegrityCheckFailedError):
    return JSONResponse(status_code=422, content={"error": "IntegrityCheckFailed", "detail": str(exc)})

# Endpoints
@app.post("/inference", response_model=ExecutionReceipt)
async def execute_inference(request: InferenceRequest, engine: InferenceEngine = Depends(get_inference_engine)):
    return await engine.execute(request)

@app.get("/health")
async def health_check(request: Request):
    if not hasattr(request.app.state, "tee_runtime") or not hasattr(request.app.state, "redis_client"):
        build_app_state(request.app)

    tee_runtime = getattr(request.app.state, 'tee_runtime', None)
    redis_client = getattr(request.app.state, 'redis_client', None)

    health_status = {
        "status": "ok",
        "tee_initialized": tee_runtime._initialized if tee_runtime else False,
        "redis": "unknown",
        "storage": "unknown"
    }

    if redis_client:
        try:
            await redis_client.ping()
            health_status["redis"] = "up"
        except Exception:
            health_status["redis"] = "down"
            health_status["status"] = "degraded"
    else:
        health_status["redis"] = "down"
        health_status["status"] = "degraded"

    health_status["storage"] = "up" if health_status["redis"] == "up" else "down"

    status_code = 200 if health_status["status"] == "ok" else 503
    return JSONResponse(status_code=status_code, content=health_status)

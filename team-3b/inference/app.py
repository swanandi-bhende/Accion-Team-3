import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, Request
from fastapi.responses import JSONResponse
import redis.asyncio as redis

from common.models import InferenceRequest, ExecutionReceipt
from common.exceptions import (
    ModelNotFoundError, 
    TimeoutExceededError, 
    TEEUnavailableError, 
    IntegrityCheckFailedError,
    StorageUnavailableError
)
from common.config import get_settings
from common.model_registry import ModelRegistry
from common.storage.ipfs_backend import IPFSBackend
from inference.tee_runtime import TEERuntime
from inference.engine import InferenceEngine

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    
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
    
    yield
    
    tee_runtime.shutdown()
    await redis_client.close()

app = FastAPI(title="Inference Service", lifespan=lifespan)

# Dependency Injector
def get_inference_engine(request: Request) -> InferenceEngine:
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
    tee_runtime = getattr(request.app.state, 'tee_runtime', None)
    redis_client = getattr(request.app.state, 'redis_client', None)
    
    health_status = {
        "status": "ok",
        "tee_initialized": tee_runtime._initialized if tee_runtime else False,
        "redis": "unknown",
        "storage": "unknown"
    }
    
    # Check Redis
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
        
    # We could check IPFS storage here via a simple request if we had a ping method, 
    # but let's just mark it degraded if redis is down for now, or assume up
    health_status["storage"] = "up" # Simplified for this check

    status_code = 200 if health_status["status"] == "ok" else 503
    return JSONResponse(status_code=status_code, content=health_status)

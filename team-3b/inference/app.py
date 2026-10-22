from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.datastructures import State

from common.config import get_settings
from common.exceptions import (
    IntegrityCheckFailedError,
    ModelExecutionError,
    ModelNotFoundError,
    TEEUnavailableError,
    TimeoutExceededError,
)
from common.model_registry import ModelRegistry
from common.models import ExecutionReceipt, InferenceRequest
from common.storage.ipfs_backend import IPFSBackend
from inference.engine import InferenceEngine
from inference.jobs import (
    FINISHED_STATUSES,
    POLL_AFTER_S,
    InvalidJobError,
    JobConflictError,
    JobCreate,
    JobService,
    summary,
)
from inference.tee_runtime import TEERuntime
from proof_generator.zkml_prover import ZkMLProver


def build_app_state(app: FastAPI) -> State:
    """Create the Redis, storage, registry, TEE and engine objects on app.state.

    Idempotent: returns the existing state unchanged if the engine is already built.
    """
    if hasattr(app.state, "inference_engine"):
        return app.state

    settings = get_settings().require("redis_url", "tee_mode", "model_storage_url")

    redis_client = redis.Redis.from_url(settings.redis_url or "")
    storage_backend = IPFSBackend(node_url=settings.model_storage_url or "")
    model_registry = ModelRegistry(redis_client, storage_backend)

    tee_runtime = TEERuntime(settings.tee_mode or "")
    tee_runtime.initialize()

    inference_engine = InferenceEngine(
        registry=model_registry,
        tee=tee_runtime,
        redis_client=redis_client,
        timeout_s=settings.tee_inference_timeout_s,
        sealed_key=settings.sealed_key_bytes(),
    )
    zkml_prover = ZkMLProver(
        model_registry,
        proving_key_store=settings.zkml_proving_key_store,
        backend=settings.zkml_backend,
    )

    app.state.redis_client = redis_client
    app.state.storage_backend = storage_backend
    app.state.model_registry = model_registry
    app.state.tee_runtime = tee_runtime
    app.state.inference_engine = inference_engine
    app.state.zkml_prover = zkml_prover
    app.state.job_service = JobService(
        redis_client,
        inference_engine,
        tee_runtime,
        zkml_prover,
        max_concurrent=settings.pipeline_max_in_flight,
        timeout_s=settings.tee_inference_timeout_s + ZkMLProver.PROOF_TIMEOUT_S,
    )
    return app.state


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """FastAPI lifespan hook: build app state, resume unfinished jobs, and release resources on shutdown."""
    state = build_app_state(app)
    jobs = get_job_service_from_state(state)
    await jobs.resume()
    yield
    await jobs.shutdown()
    tee_runtime = getattr(state, "tee_runtime", None)
    redis_client = getattr(state, "redis_client", None)
    if tee_runtime is not None:
        tee_runtime.shutdown()
    if redis_client is not None:
        await redis_client.close()


def get_job_service_from_state(state: State) -> JobService:
    """Return the job service on ``state``, building it from the other components if absent.

    Tests that set the engine and TEE on app.state by hand do not go through
    ``build_app_state``, so the service is built from whatever the state already holds.
    """
    if not hasattr(state, "job_service"):
        state.job_service = JobService(
            state.redis_client,
            state.inference_engine,
            state.tee_runtime,
            state.zkml_prover,
        )
    service: JobService = state.job_service
    return service

app = FastAPI(title="Inference Service", lifespan=lifespan)

# Dependency Injector
def get_inference_engine(request: Request) -> InferenceEngine:
    """FastAPI dependency that returns the shared InferenceEngine, building app state if needed."""
    if not hasattr(request.app.state, "inference_engine"):
        build_app_state(request.app)
    engine: InferenceEngine = request.app.state.inference_engine
    return engine

# Exception Handlers
@app.exception_handler(ModelNotFoundError)
async def model_not_found_handler(request: Request, exc: ModelNotFoundError) -> JSONResponse:
    """Map ModelNotFoundError to a 404 JSON response."""
    return JSONResponse(status_code=404, content={"error": "ModelNotFound", "detail": str(exc)})

@app.exception_handler(TimeoutExceededError)
async def timeout_handler(request: Request, exc: TimeoutExceededError) -> JSONResponse:
    """Map TimeoutExceededError to a 408 JSON response."""
    return JSONResponse(status_code=408, content={"error": "TimeoutExceeded", "detail": str(exc)})

@app.exception_handler(TEEUnavailableError)
async def tee_unavailable_handler(request: Request, exc: TEEUnavailableError) -> JSONResponse:
    """Map TEEUnavailableError to a 503 JSON response."""
    return JSONResponse(status_code=503, content={"error": "TEEUnavailable", "detail": str(exc)})

@app.exception_handler(IntegrityCheckFailedError)
async def integrity_check_handler(request: Request, exc: IntegrityCheckFailedError) -> JSONResponse:
    """Map IntegrityCheckFailedError to a 422 JSON response."""
    return JSONResponse(status_code=422, content={"error": "IntegrityCheckFailed", "detail": str(exc)})


@app.exception_handler(ModelExecutionError)
async def model_execution_handler(request: Request, exc: ModelExecutionError) -> JSONResponse:
    """Report a model that cannot run on the input as 422 ModelExecutionFailed."""
    return JSONResponse(status_code=422, content={"error": "ModelExecutionFailed", "detail": str(exc)})

# Endpoints
@app.post("/inference", response_model=ExecutionReceipt)
async def execute_inference(request: InferenceRequest, engine: InferenceEngine = Depends(get_inference_engine)) -> ExecutionReceipt:
    """Run one inference request and return its ExecutionReceipt.

    Domain errors are turned into HTTP responses by the exception handlers above.
    """
    return await engine.execute(request)

def get_job_service(request: Request) -> JobService:
    """FastAPI dependency that returns the shared job service, building app state if needed."""
    if not hasattr(request.app.state, "inference_engine"):
        build_app_state(request.app)
    return get_job_service_from_state(request.app.state)

@app.post("/jobs", status_code=202)
async def create_job(body: JobCreate, service: JobService = Depends(get_job_service)) -> JSONResponse:
    """Accept an inference job and return 202 at once. Poll GET /jobs/{job_id} for the result.

    Posting the same (task_id, input_hash) again returns the same job. The same key for a
    different model is a 409.
    """
    try:
        job, created = await service.submit(body.task_id, body.input_hash, body.model_id, body.input_data)
    except InvalidJobError as error:
        return JSONResponse(status_code=422, content={"error": "InvalidJob", "detail": str(error)})
    except JobConflictError as error:
        return JSONResponse(status_code=409, content={"error": "JobConflict", "detail": str(error)})
    return JSONResponse(
        status_code=202,
        content=summary(job, created),
        headers={"Location": f"/jobs/{job['job_id']}"},
    )

@app.get("/jobs/{job_id}")
async def get_job(job_id: str, service: JobService = Depends(get_job_service)) -> JSONResponse:
    """Report a job's status. Once it is completed, the response carries the receipt.

    The receipt holds the execution receipt, the TEE attestation and the zkML proof, in the
    wire formats the verification API accepts.
    """
    job = await service.get(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "JobNotFound", "detail": job_id})
    headers = None if job["status"] in FINISHED_STATUSES else {"Retry-After": str(POLL_AFTER_S)}
    return JSONResponse(status_code=200, content=job, headers=headers)

@app.get("/health")
async def health_check(request: Request) -> JSONResponse:
    """Report service health as JSON.

    Returns 503 unless Redis answers a ping. Storage is reported as up only in that case.
    """
    if not hasattr(request.app.state, "tee_runtime") or not hasattr(request.app.state, "redis_client"):
        build_app_state(request.app)

    tee_runtime: TEERuntime | None = getattr(request.app.state, 'tee_runtime', None)
    redis_client = getattr(request.app.state, 'redis_client', None)

    health_status = {
        "status": "ok",
        "tee_initialized": tee_runtime.initialized if tee_runtime else False,
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

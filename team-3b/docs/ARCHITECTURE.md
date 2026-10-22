# Architecture Notes

## How the pipeline works end-to-end

`inference/pipeline.py` is the composition layer. A `Pipeline` owns one instance
each of the event listener, inference engine, TEE runtime, zkML prover, proof
submitter, and (optionally) the verification interface, and drives a task through
all of them. Two entrypoints reach it:

- `python -m inference.pipeline` builds it from the environment via
  `build_pipeline()` and runs it; and
- `build_pipeline(settings)` / `Pipeline(...)` are used directly by the
  integration tests, with injected fakes where a live service is not available.

Because both paths call the same wiring function, the composed service and the
tests cannot drift apart.

### The five stages and the artifact at each boundary

| # | Stage | Called as | Artifact handed on |
|---|-------|-----------|--------------------|
| 1 | **Event Listener** | `EventListener._process_events()` | task dict with `created` and `funded` entries |
| 2 | **Inference Engine + TEE Runtime** | `InferenceEngine.execute(request)` | `ExecutionReceipt` (`execution_id`, `input_hash`, `output_hash`, `output`, `model_cid`) |
| 3 | **Proof Generator** | `TEERuntime.generate_attestation(input, output)`, then `ZkMLProver.generate_proof(receipt)` | `AttestationReport` (Ed25519 signed) + `ProofArtifact` (`public_inputs`: input/output/model hash, architecture) |
| 4 | **Proof Submitter** | `ProofSubmitter.submit_proof(task_id, attestation, proof, model_cid)` | `PendingProof` buffered for the batch window, then `TaskManager.submitProofs(...)` on chain |
| 5 | **Verification** | `VerificationInterface.verify(VerificationRequest)` | `VerificationResponse(result=..., reason_code=...)` |

The listener is what decides that a task is ready: it only dispatches once it has
seen **both** `TaskCreated` and `TaskFunded` for the same task id, so a task that
was never funded is never executed. Stage 5 re-verifies the proof bytes through
the same public interface an external party would use, which is what makes the
end-to-end test evidence rather than a self-consistency check.

### Failure policy: every boundary decides, nothing is left to chance

Each stage boundary catches its own typed exceptions and records an explicit
outcome on the task instead of letting an exception tear down the poll loop. The
outcome vocabulary is the `OUTCOME_*` constants in `inference/pipeline.py`:

| Failure at the boundary | Decision | Outcome recorded |
|---|---|---|
| `ModelNotFoundError` | log and drop | `dropped_model_not_found` |
| `TimeoutExceededError` (30 s inference budget) | log and drop — a retry only re-blows the deadline | `dropped_inference_timeout` |
| `IntegrityCheckFailedError` | log and drop — the model binary does not match its committed hash | `dropped_integrity_failure` |
| `TEEUnavailableError`, `StorageUnavailableError`, `OSError` | bounded retry (3 attempts, 0.5 s/1 s backoff), then drop | `dropped_tee_unavailable` |
| `TimeoutError` from proof generation (120 s budget) | log and drop — every other queued task keeps flowing | `dropped_proof_timeout` |
| `UnsupportedArchitectureError` | log and drop — no circuit exists for this architecture | `dropped_unsupported_architecture` |
| proof submission exhausts its retries | dead-letter to `proof_submitter:dlq` and continue | `dropped_submission_failed` |
| proof from a backend that is not allowed on chain (development commitment by default) | refuse, nothing reaches the chain, and continue | `dropped_proof_not_submittable` |
| no proving bundle for the model (EZKL backend) | log and drop until the bundle is built | `dropped_proof_keys_missing` |
| the circuit's output differs from the TEE's beyond the quantization tolerance | log and drop, since the two describe different computations | `dropped_proof_inconsistent` |
| the EZKL toolchain fails to prove an accepted input | log and drop | `dropped_proof_failed` |
| anything unexpected | caught by a last-resort guard, logged with a traceback | `dropped_inference_error` |

A drop is always logged with the correlation ids below, so a task that never
settles is explainable from the logs alone. Stages that were never reached leave
their timing fields `None`, which is how a reader can tell *where* a task stopped.

### Correlation ids (Step 16.5)

Two ids travel with every task:

- **`task_id`** — the on-chain task id, present from the moment the listener sees
  the event. It is the join key against Team A's chain data.
- **`execution_id`** — the UUID generated inside `InferenceEngine.execute()` and
  carried on the `ExecutionReceipt`, and therefore on the durable
  `inference:audit_log` Redis Stream entry.

Every pipeline log line carries `task_id=... execution_id=...` once the
attestation exists, so a single grep on a task id reconstructs the whole journey:

```
PIPELINE_ENQUEUED        task_id=...
PIPELINE_INFERENCE_RETRY task_id=... attempt=1/3 delay_s=0.5
PIPELINE_PROOF_READY     task_id=... execution_id=... architecture=mlp public_inputs={...}
PIPELINE_PROOF_BUFFERED  task_id=... execution_id=... proof_hash=... buffered_s=0.001
PIPELINE_SETTLED         task_id=... execution_id=... tx_hash=0x... confirmation_s=0.12
PIPELINE_VERIFICATION    task_id=... execution_id=... result=True reason_code=None
PIPELINE_TASK_COMPLETE   task_id=... execution_id=... outcome=submitted total_s=1.234
```

The `execution_id` is not available to the listener stages because the engine
generates it, so pre-inference log lines carry `task_id` only and the id is
stitched in as soon as the receipt exists. `TaskRecord.execution_id` is `None` for
a task that was dropped before inference returned — which is itself the signal
that the failure happened early.

### Backpressure and concurrency limits (Step 16.6)

Three limits, all derived from `PIPELINE_MAX_IN_FLIGHT` (default 4):

1. a bounded `asyncio.Queue` of size `max_in_flight * 2` between the listener and
   the executor, so a burst of funded tasks buffers instead of stampeding the TEE;
2. a worker pool of `max_in_flight` tasks consuming that queue; and
3. an `asyncio.Semaphore(max_in_flight)` around the inference stage, so TEE
   concurrency stays capped even for a caller that bypasses the queue.

Backpressure is never silent: when the queue is full the admission path logs
`PIPELINE_BACKPRESSURE` with the queue depth, the cap and the in-flight count
before it blocks on `put`. `Pipeline.stats()` exposes `queued`, `in_flight`,
`peak_inference_in_flight`, `throttled_events` and the per-outcome counts, so a
throttling event during a load test is visible rather than just showing up as
mysterious slowness.

### Latency budget (Step 16.9)

`Pipeline.latency_report()` returns min/p50/p95/max/mean for dispatch-to-completion
and for each stage. The worst-case budget implied by the design is:

| Segment | Budget |
|---|---|
| inference (TEE) | 30 s (`TEE_INFERENCE_TIMEOUT_S`) |
| attestation + zkML proof | 120 s (`ZkMLProver.PROOF_TIMEOUT_S`) |
| batch window before submission | 10 s (`PROOF_BATCH_WINDOW_S`) |
| on-chain confirmation | the chain's block time |

The pipeline's own numbers against this budget, measured with the real mock
chain, are recorded in `docs/CHECKPOINTS.md` under V1.16.

### What this does *not* prove

Stated plainly so the diagram is not read as more than it is: the enclave is
`EnclaveSim` (no hardware attestation), the proof backend is the explicitly
labelled `development-commitment` construction rather than zkML, and the chain is
a local Hardhat node running mock contracts. The pipeline proves the *plumbing*,
the failure taxonomy, and the correlation and backpressure behaviour. See
`TEE_NOTES.md` and `ZKML_SPIKE.md` for what is missing to call any of this
production-grade.

## Inter-component message flow: queue vs direct call (Step 16.2)

The design doc describes one Docker service per component, so the wiring choice
was made to survive that split rather than to be convenient in one process.

**Decision: a bounded queue between the listener and the executor, and direct
awaited calls between the stages of a single task.**

Reasoning, per boundary:

- **Listener -> executor: queue.** The listener's loop polls the chain and must
  keep doing so. TEE inference takes up to 30 s and proof generation up to 120 s,
  so a direct call would stall the poll loop for up to 150 s per task and make the
  listener's own liveness (and its reconnect/backoff logic) hostage to the slowest
  stage. The queue also gives admission control a natural place to live: it is
  where the burst is buffered and where throttling becomes observable.
- **Stage -> stage within a task: direct call.** Each stage consumes the previous
  stage's artifact — the prover cannot run before the receipt exists, and nothing
  can be submitted before the proof exists. A queue between them would add a hop
  of latency, force every intermediate artifact to be serialized and checkpointed
  so a worker restart could resume mid-task, and buy no decoupling, because the
  dependency is strictly sequential anyway.

Alternatives considered and rejected:

- **All direct calls.** Simplest to write, but couples the listener's liveness to
  the slowest stage and gives no place to bound concurrency, which is exactly the
  failure 16.6 exists to prevent.
- **Redis Streams between every stage.** Closest to the eventual multi-container
  topology, but it multiplies the moving parts an integration test must stand up
  and adds at-least-once redelivery semantics that every stage would then need to
  make idempotent. Premature for the current failure model.
- **Celery or similar.** A separate broker and worker deployment is a Step 17
  conversation; pulling it in now would hide the pipeline's own behaviour behind
  framework defaults.

**How this maps onto Step 17.** The queue is the single seam. `Pipeline.enqueue`
is already the listener's dispatch target, and the per-task stage chain is already
a plain awaited sequence, so the multi-container deployment replaces
`asyncio.Queue.put`/`get` with `XADD`/`XREADGROUP` against Redis Streams and
leaves every stage's logic untouched. The same reasoning is why the failure
vocabulary is per-stage rather than global: when the stages become separate
services, a stage that fails is a container that failed, and the outcome names
already say which one.

## Configuration as a single source of truth (Step 16.3)

Every environment variable the subsystem reads is declared exactly once, on the
typed `Settings` model in `common/config.py`; module-level `os.getenv` calls are
gone. Given the one-service-per-component design, the fail-fast check is
**per service, not global**: each entrypoint calls
`get_settings().require("a", "b", ...)` with exactly the variables it uses.

That is a deliberate departure from a global import-time check. A global check
would force every container to carry every variable — a federated-learning worker
would need blockchain addresses it never reads — and, worse, a variable missing
for an unrelated service would crash the service that does not need it. The thing
16.3 actually asks for, "fail fast with a clear, itemized error listing every
missing/invalid variable at once", is preserved: `require()` reports **all**
offending variables in one `ConfigurationError`, so a misconfigured deployment is
fixable in a single pass rather than one variable per restart.

## zkML Proofs in Plain Language

The proof-generator boundary receives an execution receipt and model metadata.
For a supported architecture, a production zkML backend will compile or load a
circuit, use the model weights as a private witness, and expose the input hash,
output hash, and committed model hash as public inputs. A verifier can then
check that the committed model was run on the committed input and produced the
committed output without receiving the model weights.

That proof does **not** show that the model is good, accurate, safe, unbiased,
or trained on legitimate data. It also does not prove that the input was a
truthful representation of the outside world. It proves a bounded computation
against the exact model and inputs committed by the protocol.

The current repository contains an explicitly labeled development-commitment
backend while EZKL is evaluated. It is useful for testing the API and data
flow, but it is not a cryptographic zkML proof and is not suitable for
production settlement. See `ZKML_SPIKE.md` for the toolchain evaluation and
`TEE_NOTES.md` for the hardware-attestation status.

## Federated Learning Orchestration

`federated_learning/orchestrator.py` coordinates federated-learning rounds with
the Flower framework. It builds a Flower `FedAvg` strategy, distributes the
current global model to registered clients each round, aggregates the client
updates, and records a `RoundProvenance` entry per round. The final aggregated
model is stored through the Model Registry, which returns a CID the rest of the
pipeline can consume.

Key behaviors:

- **Aggregation.** FedAvg is the default strategy. `_build_strategy` selects it
  from an explicit registry and raises `ValueError` for unknown names rather
  than silently defaulting, so a config typo cannot masquerade as a normal run.
  The registry also offers Byzantine-robust options (`fedmedian`,
  `fedtrimmedavg`, `krum`) and a differential-privacy option (`dp_fedavg`).
  `weighted_average` is an independent reference implementation of the FedAvg
  arithmetic, tested directly so the framework default is not taken on faith.
- **Fault tolerance.** The strategy uses `min_fit_clients=1`: a round proceeds
  with whatever subset of clients responded within `round_timeout`, and the
  specific clients that timed out are logged by ID so a straggler is
  diagnosable after the fact. Malformed or wrong-shaped updates are rejected and
  excluded rather than crashing aggregation.
- **Zero responders.** If no client responds in a round, the run fails with
  `NoClientsRespondedError`. `min_fit_clients=1` means "at least one"; with zero
  updates the global model is undefined, so we fail loudly instead of silently
  advancing a stale model.
- **Provenance.** Each `RoundProvenance` record (round number, participating
  client count, strategy, duration, resulting model hash) is persisted to Redis
  as the round completes, so a crash on round 7 of 10 still leaves a durable
  record of rounds 1-6.

### Current scope: clients are simulated; a real transport is tested, SuperLink is not

In the default path clients are **simulated locally**:
`federated_learning/simulated_client.py` provides real
`flwr.client.NumPyClient` instances, so Flower's own client code paths are
exercised, but they train on a synthetic regression dataset partitioned across
clients rather than real participant data.

There are two network paths on top of that:

- `federated_learning/network_runner.py` runs Flower's **gRPC** transport with a
  real server process and real client processes over sockets. It is exercised in
  `tests/integration/test_fl_real_network.py` (a 3-client, multi-round job whose
  loss must fall, and a run that survives a client process death mid-training).
  Flower 1.39 deprecates this API in favour of SuperLink, so it is the
  development and verification transport, not the chosen production one.
- `federated_learning/server_app.py` / `client_app.py` use Flower's **SuperLink**
  model for multi-host production and capture provenance inside
  `ProvenanceStrategy`. This path has **not** been validated end-to-end here —
  that requires a SuperLink runtime.

The remaining production gaps are:

1. validate the SuperLink deployment in staging (transport + round timing);
2. client authentication and authorization so only approved participants can
   contribute updates;
3. TLS and quota/rate governance for the transport; and
4. cryptographic **secure aggregation** so the server never observes an
   individual participant's raw weights (robust strategies and DP are already
   available; they are integrity-oriented, not confidentiality-oriented).

Behaviour under real network latency, packet loss, or many clients is unmeasured:
the gRPC runs above were taken on loopback with three clients.

See `FEDERATED_LEARNING.md` for deployment steps, the strategy table, and the
secure-aggregation roadmap.

### How the output feeds the rest of the pipeline

A completed training job serializes the aggregated weights, uploads them via
`ModelRegistry.upload_model`, and registers them under the task's `model_id`.
That CID is then immediately usable by the Inference Engine and TEE Runtime:
an integration test trains a model here and confirms it is retrievable, its
hash matches, and it executes through the attested inference path. From there
the same model flows into zkML proof generation and on-chain settlement like
any other registered model.

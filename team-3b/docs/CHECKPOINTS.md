# Internal Checkpoints

## V1.6 Core Loop Review

Properties 1-5, 19, and 20 now have explicit Hypothesis or unit coverage. In
plain language, they guarantee that successful inference returns a complete
receipt with correct input/output hashes (Property 1), concurrent requests
remain isolated (Property 2), unknown model identifiers fail before execution
and audit logging (Property 3), model bytes are verified before entering the
TEE and decrypted bytes are securely erased (Property 4), attestation reports
contain the expected identity and hashes (Property 5), registry lookups return
the registered version and CID (Property 19), and a CID must resolve to the
same bytes that were registered, including when the referenced content is
tampered with (Property 20).

The local coverage run used:

```text
pytest --cov=inference --cov=common --cov-report=term-missing -q -p no:warnings
```

It produced 27 passing tests. The component results were Inference Engine
100%, TEE Runtime 93%, and Model Registry 98%. The combined `inference` and
`common` total was 76%, with unimplemented or integration-only storage/config
paths accounting for most of the remaining lines. The error branches for
`ModelNotFoundError`, `TimeoutExceededError`, `TEEUnavailableError`,
`IntegrityCheckFailedError`, and `StorageUnavailableError` are each exercised
by at least one test.

The concurrency property uses an `asyncio.Event` barrier to force request B to
enter while request A is paused. The integrity property spies on
`secure_erase`, and the CID property covers both unresolved and tampered
content. Focused edge tests cover empty input, unusual model identifiers, the
exact 500 MB boundary, the 30.0-second timeout configuration, and reconnecting
the registry after closing its Redis client.

The current limitations are deliberate: the TEE remains a simulator rather
than hardware-backed SGX/SEV, storage integrations remain mocked in tests, and
the Redis restart test skips when no local Redis service is available. CI
provides a real Redis 7 service container and runs the unit/property gate on
every push and pull request. Proof generation remains gated on this checkpoint.

No commit or tag was created during this session. After reviewing the CI run,
the team can create the requested marker with:

```text
git tag checkpoint-1-core-loop
```

## V1.7 zkML Risk Review

The proof-generator API, architecture metadata, circuit cache, 120-second
timeout, Properties 6-8, malformed-receipt tests, and manual sanity script are
implemented. The local spike found Circom and snarkjs binaries but no EZKL,
ONNX, or ONNX Runtime installation. Those packages are now installed and
importable in the project venv, and a real tiny 2-layer MLP EZKL proof has now
verified successfully. Proof generation took 11.29 seconds and verification
took 0.076 seconds; the proof was 17,650 bytes. EZKL is the provisional
production candidate; the current
`development-commitment` backend is deliberately not a zkML proof and is gated
from production settlement. Transformer-small feasibility and measured proving
times remain open mentor-review risks.

For mentor review, tiny real EZKL round trips now verify for linear, MLP, CNN,
and transformer-small. Proving times are 9.07 s, 9.36 s, 10.00 s, and 37.01 s
respectively, all under 120 seconds. These are intentionally tiny fixtures, so
the remaining question is how proof time and proving-key size scale with the
production model sizes. Guidance is still needed on whether 120 seconds is a
hard maximum or a target and what transformer parameter/operator budget should
be required before production.

## V1.11 Wave 2 On-Chain Pipeline Review

Properties 12-18 cover the on-chain-facing pipeline: event fields are
extracted exactly, dispatch requires both TaskCreated and TaskFunded, persisted
block progress limits replay work, reconnect delays are capped at 60 seconds,
proof batches carry aligned task/proof/model/result arrays, proofs in one
window share a transaction, and exhausted retries are preserved in Redis DLQ
entries with a permanent-failure alert.

The composite mock-chain test runs TaskCreated and TaskFunded through deployed
Hardhat contracts, dispatches a fabricated receipt/proof pair, and confirms a
successful submitProofs transaction. CI starts real Redis and Hardhat services,
runs the properties and composite integration, and measures coverage for both
components. The audit deliberately broke the ingestion expiry guard and batch
timer reset; each test failed, and both implementations were restored.

Additional coverage simulates duplicate events at new block numbers, concurrent
DLQ failures, business-logic transaction reverts, abandoned-task cleanup, and
real Redis persistence.

Known limitations remain: the reorg test is a one-event duplicate simulation,
not a deep multi-block reorganization; the contracts are still Team A mocks;
and the listener uses event filters over its configured WebSocket provider
rather than a finalized production subscription strategy. Team A addresses,
ABIs, finality/reorg policy, and signer/network configuration are still needed
for production integration.

No commit or tag was created. The requested `checkpoint-2-onchain-pipeline`
tag can be created by the repository owner after CI review.

## V1.15 Wave 3 Federated Learning Review

Properties 9-11 now have a real-transport counterpart, not just in-process
coverage. The single-process tests from Step 14 exercise the genuine Flower
`NumPyClient` and `Strategy` objects, but they call `fit`/`aggregate_fit`
directly. This checkpoint runs an actual gRPC Flower server with three real
client **processes** exchanging serialized messages over sockets, which is the
layer where integration bugs actually live.

`tests/property/test_fl_orchestrator.py` already covered Property 9 against both
our own reference mean and Flower's `FedAvg.aggregate_fit` output, so the
arithmetic was not bypassing Flower. The outstanding gap was transport: nothing
ran the real server/client stack. `tests/integration/test_fl_real_network.py`
closes it, and `federated_learning/network_runner.py` is a new runnable entrypoint
(`server` and `client` roles) for that path.

The audit surfaced two genuine defects in the network path, both now fixed:

1. `min_available_clients` was hard-coded to `num_clients`. Flower's client
   manager blocks until that many clients are connected before each round, so a
   real client disconnect **wedged the server forever** -- directly violating
   Requirement 4.5. `FLTaskConfig.min_available_clients` is now a validated,
   optional field (default: the full roster), so a deployment that must survive
   dropouts can lower it.
2. `ServerConfig` was built without `round_timeout` on the SuperLink path, so
   `FLTaskConfig.round_timeout` was silently ignored and a killed client stalled
   the round. Both the SuperLink and gRPC runners now pass it through.

Measured behaviour of a real 3-client, 4-round job on this machine (Python
3.14, local loopback, no artificial network delay):

```text
round 1: 0.4603   (3 clients participated)
round 2: 0.2736
round 3: 0.1633
round 4: 0.0979
wall clock: ~0.9 s for the whole run (client process startup excluded)
```

Loss decreases monotonically, which is the sanity check that the aggregation and
update loop is wired correctly rather than merely error-free. Killing one client
process after round 1 leaves participation at 3, 2, 2, 2 with no exception
propagating to the caller, and provenance records the reduced count exactly.

Step 15.5 round-trips a trained model all the way through: `run_training` stores
it, `ModelRegistry.get()` resolves the CID, the stored binary hashes to the
registered hash *and* to the final round's `resulting_model_hash`, and
`TEERuntime.execute_inference()` accepts that exact binary and returns output. A
blob whose plaintext does not match the expected model hash is refused with
`IntegrityCheckFailedError`.

Step 15.7 kills a *separate* process running `run_training` in the middle of a
5-round job (via `scripts/fl_crash_harness.py`) and confirms the provenance for
the completed rounds is still in Redis and no model was registered, since the
run never reached its final round. This tests the Step 14.5 incremental-write
decision under a real failure instead of asserting the code mentions it.

Real gRPC tests are marked `fl_network` and run in a dedicated, longer-timeout
`fl-integration` CI job with a Redis service; the fast unit/property gate
excludes them with `-m "not fl_network"`. The dedicated FL job runs 41 tests, of
which two drive a real gRPC server with real client processes and one SIGKILLs a
real training process mid-run; the full suite is 161 passing tests.

Known limitations, stated plainly: the clients are still simulated (synthetic
linear regression, not real participant data); everything was measured on
loopback on one machine, so behaviour under real network latency, packet loss, or
more than a handful of clients is untested; security aggregation is not
implemented; and the SuperLink deployment path remains import- and unit-tested
only, because it needs a running SuperLink control plane. The `flwr` legacy
gRPC API used by `network_runner` is itself deprecated in Flower 1.39 in favour
of SuperLink, so it is a development/verification transport rather than the
chosen production one.

No commit or tag was created. The requested `checkpoint-3-federated-learning`
tag can be created by the repository owner after CI review:

```text
git tag checkpoint-3-federated-learning
```

## V1.16 Wave 3 Full Pipeline Review

Every stage had its own verification by the end of Wave 2, but nothing owned the
chain from a funded task to a settled transaction. `inference/pipeline.py` is
that owner: it wires `EventListener -> InferenceEngine -> TEERuntime ->
ZkMLProver -> ProofSubmitter` (plus the Step 12 verification interface) from a
single config object, and records each task's outcome whichever way it goes.

**Coupling (Step 16.2).** The listener hands off through a bounded
`asyncio.Queue`; the stages *within* one task use direct awaited calls. The queue
exists because the chain-poll loop must never block behind a slow TEE inference,
and because it is the exact seam Step 17 replaces with Redis Streams — the
in-process wiring and the multi-container deployment then keep one shape instead
of diverging. A queue *between* stages was rejected: each stage consumes the
previous stage's object, so an intermediate queue would add latency and a
mid-flight checkpointing burden while decoupling nothing. Concurrency is capped
twice on purpose: `max_in_flight` workers bound end-to-end concurrency, and a
semaphore around the inference stage bounds TEE concurrency even for callers that
bypass the queue.

**Config (Step 16.3).** `common/config.py` is the only place that reads the
environment. `get_settings()` is cached and typed, and `require()` fails with an
itemized error naming *every* missing variable at once, so integration debugging
is not one-variable-at-a-time.

Writing the end-to-end test surfaced two real defects, both now fixed:

1. `Pipeline` read `task["task_id"]`, but the listener's dispatch payload nests
everything under `created`/`funded`. Every real dispatch therefore died with a
`KeyError` before inference ever ran. `EventListener._dispatch_to_engine` had the
same latent bug on its own default dispatch path.
2. `TEERuntime.execute_inference` let an AEAD tag failure propagate raw, so a
model blob that cannot be authenticated under its committed key surfaced as
"unexpected error" instead of an integrity failure. That is the same violation as
a plaintext hash mismatch, and it now raises `IntegrityCheckFailedError`.

`ProofSubmitter` also gained `flush()` and `buffered_task_ids()`: without a way
to force a batch out, a confirmed transaction could only be asserted by waiting
out the 10 s batch window.

**Latency (Step 16.9).** `tests/integration/test_full_pipeline_e2e.py` runs six
funded tasks through the real mock chain, each settled by its own flush so every
sample is a complete dispatch-to-confirmation journey (with a shared flush only
the task that triggered it has an attributable latency). Seconds, local Hardhat
node, in-memory storage double, dev-commitment proof backend:

```text
                 min       p50       p95       max       mean
inference_s      0.0253    0.0291    0.0501    0.0501    0.0318
proof_s          0.000598  0.000660  0.000953  0.000953  0.000715
confirmation_s   0.0194    0.0211    0.0243    0.0243    0.0213
total_s          0.0455    0.0509    0.0754    0.0754    0.0540
```

The design budget is 30 s inference + 120 s zkML + 10 s batch window + on-chain
confirmation. The observed worst case (0.075 s) sits about 2000x inside that, but
it is *not* evidence about the budget: inference here is the simulated enclave
subprocess (~29 ms), the zkML "proof" is the dev-commitment backend (~0.7 ms)
rather than real proving, storage is an in-memory double, and the chain is a
local node with instant blocks. What the measurement does establish is the
orchestration cost Step 16 is responsible for — hand-off to the enclave
subprocess, hashing, artifact serialization, batch flush, and the transaction
round trip — which accounts for the ~54 ms p50 total. Real proving time is
unchanged by this step and remains bounded by the prover's own 120 s timeout.

**Failure injection (Steps 16.6 / 16.8).**
`tests/integration/test_pipeline_resilience.py` covers the paths *between*
stages, which is where bugs hide from component tests:

- nine tasks through three workers: peak concurrent TEE inference is exactly 3,
the six-slot queue throttles with `PIPELINE_BACKPRESSURE` logged, and the queue
drains to empty — a burst cannot stampede the enclave.
- a correctly sealed blob registered under the wrong hash: that task is dropped
as `dropped_integrity_failure` with `execution_id` and `proof_s` still null
(proving it never reached a receipt), and the next task settles normally. A hash
mismatch cannot succeed on retry, so it is never retried.
- storage returning bytes that are not the committed sealed artifact: dropped as
an integrity failure rather than through the catch-all error bucket.
- a zkML timeout, both from a double and from the real `ZkMLProver` with
`timeout_s=0`: the timed-out task is dropped after inference completed, and the
healthy task still reaches the submitter.
- a proof batch that reverts on every attempt: it is retried `MAX_RETRIES` times,
lands in the Redis DLQ with its task ids and error text, and the *next* task
settles on chain with status 1 — the pipeline is not stuck.

Correlation ids (Step 16.5) run through every stage: `PIPELINE_ENQUEUED`,
`PIPELINE_PROOF_READY`, `PIPELINE_PROOF_BUFFERED`, `PIPELINE_SETTLED`,
`PIPELINE_VERIFICATION` and `PIPELINE_TASK_COMPLETE` all carry `task_id`, and
everything after inference also carries the generated `execution_id`, so one
task's journey is traceable from logs alone. The diagrams in
`docs/diagrams/pipeline.md` and the new sections of `docs/ARCHITECTURE.md` are
the map for that.

The suite is now 170 collected tests, 9 of them added by this step (the two
pipeline modules), and the two new modules run in the on-chain-facing CI step.
168 pass deterministically (`-m "not fl_network"`); the two real-gRPC Flower
tests in `tests/integration/test_fl_real_network.py` remain timing-sensitive — a
client process that has not finished registering when the first round is sampled
lowers that round's participation count — and one of the two failed once here for
that reason while a Docker build saturated the machine. A later full run passed
all 170; the module is excluded from the fast CI gate and runs in the dedicated
`fl-integration` job.

**What actually running the stack uncovered.** `docker compose up` had never
succeeded for this project, and bringing it up found three defects no unit test
could see: `hardhat-deploy` dialed `127.0.0.1:8545` from inside its own container
and never reached the node (it now takes the node URL from `WEB3_RPC_URL` and
waits for the RPC instead of sleeping); `event-listener` could not import a
synchronous WebSocket provider at all, because web3 8.x only ships an
*asynchronous* one — it now polls over HTTP with a logged warning, which is
equivalent for a listener that uses filters rather than subscriptions; and both consumer services read the compiled contract
ABIs from `mocks/contracts/artifacts/`, which the images did not contain, so they
now mount that directory read-only. The result is a live stack: the deploy
succeeds and writes `.env.mock` with all three addresses, `event-listener`
follows the chain to its head and records `"dispatched": true` for the triggered
task, `proof-submitter` idles against a real settlement address, the two APIs
answer on 8000/8001, and `fl-orchestrator` completes its rounds and exits 0 (it
is a one-shot job).

Known limitations, stated plainly: `docker compose` has no service for the
composed pipeline yet — the in-process wiring runs from
`python -m inference.pipeline`, and giving it a container belongs to Step 17's
multi-container work; the hand-off is still an `asyncio.Queue` rather than the
Redis Streams transport the multi-container deployment needs; the enclave is
`EnclaveSim`, so attestations are signed by a simulated measurement and nothing
here is hardware-attested; and none of it has run against Team A's contracts,
only the local mock chain.

No commit or tag was created.

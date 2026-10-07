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

# Federated Learning Orchestrator

**Requirement mapping:** Requirement 4 (Federated Learning Pipeline
Orchestration), 4.4/4.5/4.6 (model storage, fault tolerance, provenance).

This component coordinates federated-learning rounds with the
[Flower](https://flower.ai) framework, aggregates client updates, records
per-round provenance, and stores the resulting model in the Model Registry so
the rest of the pipeline can use it.

## Components

| File | Responsibility |
|------|----------------|
| `federated_learning/orchestrator.py` | `FederatedLearningOrchestrator.run_training()`, `_execute_round()`, the in-process `LocalFlowerServer`, and model storage |
| `federated_learning/strategies.py` | Aggregation registry (`build_strategy`) and the `ProvenanceStrategy` wrapper |
| `federated_learning/weights.py` | Weight serialization and the independent `weighted_average` reference |
| `federated_learning/simulated_client.py` | `NumPyClient` implementations over a partitioned synthetic dataset |
| `federated_learning/runner.py` | One-shot in-process entrypoint (used by the compose `fl-orchestrator` service) |
| `federated_learning/server_app.py` | SuperLink server entrypoint (network path) |
| `federated_learning/client_app.py` | SuperLink client entrypoint (network path) |

## Aggregation strategies

`_build_strategy(config)` resolves `config.aggregation_strategy` through an
explicit registry. An unknown name raises `ValueError` — it never silently falls
back to FedAvg, so a config typo cannot masquerade as a normal run.

| Name | Flower strategy | Use |
|------|-----------------|-----|
| `fedavg` | `FedAvg` | **Default.** Weighted mean of client updates |
| `fedmedian` | `FedMedian` | Coordinate-wise median; robust to a minority of outliers |
| `fedtrimmedavg` | `FedTrimmedAvg` | Trims the extreme `trim_fraction` of updates before averaging |
| `krum` | `Krum` | Selects the update closest to its neighbours; tolerates `num_malicious_clients` |
| `dp_fedavg` | `DPFedAvgFixed` | Adds server-side output perturbation (differential privacy) |

Relevant `FLTaskConfig` fields: `aggregation_strategy`, `trim_fraction`,
`num_malicious_clients`, `dp_clip_norm`, `dp_noise_multiplier`,
`dp_num_sampled_clients`. All have safe defaults and are validated.

> **Deprecation caveat:** Flower marks `DPFedAvgFixed`/`DPFedAvgAdaptive` as
> deprecated ("will be removed in future versions"). They work today, but a
> maintained DP path (custom clipping + noise, or a newer Flower release) should
> replace them before this becomes load-bearing in production.

## Provenance (Requirement 4.6)

After each round a `RoundProvenance` record is written to Redis **as the round
completes**, not just returned at the end — so a crash on round 7 of 10 still
leaves a durable record of rounds 1-6.

- Key: `fl:provenance:<task_id>` (a Redis list)
- Value: `RoundProvenance` JSON — `round_number`, `participating_clients`,
  `aggregation_strategy`, `round_duration`, `resulting_model_hash`

## Fault tolerance (Requirement 4.5)

- The strategy uses `min_fit_clients=1`, so a round proceeds with whatever
  subset of clients responded within `round_timeout`.
- Clients that time out are logged **by ID**, so a straggler is diagnosable
  after the fact.
- Malformed or wrong-shaped updates are rejected and excluded, never crashing
  aggregation.
- **Zero responders is a hard failure** (`NoClientsRespondedError`). With no
  updates the global model is undefined; we fail loudly rather than silently
  advancing a stale model.

## Execution paths

### In-process (tests and the compose demo)

`run_training()` drives rounds itself via `LocalFlowerServer`, which holds the
registered `NumPyClient` instances and runs them in threads with per-round
timeouts. Fast and deterministic; this is what the test suite uses.

Run it directly:

```bash
python -m federated_learning.runner
```

Environment: `REDIS_URL`, `MODEL_STORAGE_URL`, `FL_TASK_ID`,
`FL_NUM_CLIENTS`, `FL_NUM_ROUNDS`, `FL_AGGREGATION_STRATEGY`, `FL_MODEL_ID`,
`FL_MODEL_ARCHITECTURE`, `FL_ROUND_TIMEOUT`.

### Network (SuperLink) — for real remote participants

`server_app.py` and `client_app.py` wrap the same strategy registry and client
into Flower's SuperLink app model, where the server drives the rounds and
provenance is captured by `ProvenanceStrategy`.

```bash
# 1. Start the SuperLink control plane (once)
flwr-superlink --insecure

# 2. Start the server app (runs the rounds)
flwr-serverapp --insecure --serverapp-module federated_learning.server_app

# 3. Start one client app per participant
flwr-clientapp --insecure --clientapp-module federated_learning.client_app
```

Adjust the `--insecure`/TLS flags and the SuperLink address for your deployment.
Each participant is identified by Flower's `partition-id` node config.

**Note:** this deployment path is wired and import-tested, but has **not** been
exercised end-to-end here — it requires a SuperLink runtime. Validate it in a
staging environment before relying on it.

## Security posture

Robust strategies (`fedmedian`, `fedtrimmedavg`, `krum`) defend against a
bounded number of **malicious updates**; `dp_fedavg` adds **output
perturbation**. None of these is cryptographic **secure aggregation**. What is
and is not covered:

- ✅ Tolerates a minority of bad or outlying updates.
- ✅ Rounds continue when clients drop out or time out.
- ❌ The server still sees every participant's raw weights in the clear.
- ❌ No protection against an honest-but-curious server.
- ❌ No client authentication or per-client authorization yet.

### Future work: true secure aggregation

To guarantee the server never sees an individual participant's raw update:

1. **Masking / secret sharing** — each client splits its update into pairwise
   masks cancelled out during aggregation (Bonawitz et al.), so the server only
   ever sums masked values.
2. **Transport security** — TLS on the SuperLink connections
   (`start_server(certificates=...)` / SuperLink TLS configuration) and signed
   client identities.
3. **Authentication & authorization** — SuperLink accounts (`flwr login`) or
   mutual-TLS client certificates, with an allow-list of approved participants.
4. **Threat-model review** — quantify what DP noise budget is acceptable and
   record the resulting privacy guarantee. Flower 1.39 does **not** bundle
   `flwr.common.secagg`, so true secure aggregation is a deliberate
   implementation item, not a configuration flag.

Until then, treat the current aggregation as integrity-oriented (robust to bad
updates), not confidentiality-oriented.

## Tests and coverage

```bash
pytest tests/property/test_fl_orchestrator.py \
       tests/integration/test_fl_orchestrator_integration.py \
       tests/integration/test_fl_runner.py \
       tests/integration/test_fl_network_apps.py \
       --cov=federated_learning --cov-report=term-missing
```

Covers Property 9 (aggregation equals the reference mean), Property 10 (K-of-N
participation and per-round provenance counts), Property 11 (trained model CID
resolves to the exact binary), every registered strategy building and
aggregating, the provenance wrapper, the runner, and the failure scenarios
(zero responders, malformed updates, varying dropouts).

### End-to-end smoke test (no Docker required)

```bash
python scripts/fl_e2e_smoke.py
```

Runs the documented entrypoint (`python -m federated_learning.runner`) **as a
subprocess** — so a miswired entrypoint cannot hide behind an in-process import —
against a private `redis-server` on an ephemeral port (the developer's own Redis
is never touched) and an HTTP server implementing the two IPFS API calls that
`IPFSBackend` uses. For each of `fedavg` and `krum` it asserts the durable
effects: exit 0, exactly `FL_NUM_ROUNDS` provenance records with the documented
fields and real participation counts, a registered model with its architecture,
and a stored binary that hashes to both the registered hash and the final
round's `resulting_model_hash`.

Scope caveat: the IPFS stand-in verifies the **API contract** `IPFSBackend`
depends on over real HTTP (multipart framing included); it is not a Kubo node,
so it does not validate IPFS itself. The SuperLink network path is still only
import- and unit-tested — it needs a real SuperLink runtime (see above).

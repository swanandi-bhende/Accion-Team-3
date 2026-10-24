# Setup

## Prerequisites

- **Python 3.11 or newer.**
- **Docker with Compose v2** (`docker compose`). It runs Redis, IPFS, the local chain, and the services.
- **Node 20 or newer.** Only the chain tests need it, because they start a local Hardhat node.
- **Go.** Only needed if you build the 0G storage CLI yourself. The Docker images already include it.

## Install

```bash
./setup.sh
```

This creates `venv/`, installs the requirement files, then the packages under `src/` in editable mode (so the scripts can import them from anywhere), and copies
`.env.example` to `.env` if there is no `.env` yet.

## Configure

Edit `.env`. Every variable is listed in `.env.example` with a tag:

- **LOCAL**: safe as shipped.
- **REAL**: must come from the target network or another team.
- **MOCK**: points at the local chain. Replace it for a real network.

The settings you are most likely to change:

| Setting | Purpose |
| --- | --- |
| `TEE_SEALED_KEY_PATH` | File holding the key that opens sealed models. Created on first use. Back it up. |
| `MODEL_STORAGE_BACKEND` | `ipfs` (default) or `0g`. |
| `ZEROG_*` | 0G Storage: CLI path, RPC, indexer, signing key, minimum balance. The key is a secret. |
| `WEB3_RPC_URL`, `WEB3_CHAIN_ID` | The chain. Services refuse to start on a different chain id. |
| `TASK_MANAGER_ADDRESS`, `ESCROW_ADDRESS` | Team A's contracts. Blank values fall back to `MOCK_DEPLOYMENTS_PATH`. |
| `EXECUTION_TRIGGER` | `task_created_funded` (default, the spec) or `bid_accepted`. |
| `PROOF_SUBMITTER_PRIVATE_KEY` | Signs proof submissions. The all-zero value means "use the local unlocked account". |
| `ZKML_BACKEND` | `development` (default) or `ezkl`. |
| `ALERT_WEBHOOK_URL` | Optional. Dead-letter alerts are also posted here. |

Never commit `.env`. It is git-ignored.

## Run the stack

```bash
docker compose up -d
docker compose ps
```

| Service | Port | What it is |
| --- | --- | --- |
| `redis` | 6379 | State for the listener, jobs, the registry, and dead letters |
| `ipfs` | 5001 | Model storage (Kubo HTTP API) |
| `hardhat-node` | 8545 | Local chain with Team A's contracts replayed onto it |
| `team-a-contracts` | - | One-shot: installs Team A's bytecode on the local chain |
| `seed-model` | - | One-shot: registers the toy models |
| `inference-engine` | 8000 | Inference, job and challenge API |
| `pipeline` | - | Listener, inference, proving and submission in one process |
| `proof-submitter` | - | Startup checks only (chain id, prover allow-list). The pipeline submits proofs. |
| `verification-api` | 8001 | Verifies proofs |
| `fl-orchestrator` | - | Federated-learning runs |

Smoke test the running stack:

```bash
scripts/ops/smoke_test_compose.sh
```

## Run the tests

These match CI (`.github/workflows/tests.yml`):

```bash
ruff check .                                   # lint
mypy                                           # types
pytest tests/unit tests/property               # fast core suites
pytest tests/integration                       # integration; starts a local chain (Node 20+)
pytest tests/integration -m fl_network         # real Flower processes (slower)
```

The EZKL status tests read `data/zkml_spike/benchmark_result.json`, which is kept in the repo.

## Operator scripts

| Script | Use |
| --- | --- |
| `scripts/ops/smoke_test_compose.sh` | Check the running stack end to end |
| `scripts/ops/install_team_a_local.py` | Install Team A's bytecode on a local chain |
| `scripts/ops/fetch_team_a_bytecode.py` | Fetch Team A's deployed bytecode from Galileo |
| `scripts/ops/seed_model_registry.py` | Register the toy models |
| `scripts/ops/trigger_team_a_task.py` | Create and fund one task on a local chain |
| `scripts/ops/team_a_preflight.py` | Read-only readiness check for a chain with Team A's contracts |
| `scripts/ops/cross_team_e2e.py` | Drive one task through all three teams and report how far it got |
| `scripts/ops/team_c_conformance.py` | Check Team C's requests against our documented contract |
| `scripts/ops/replay_dlq.py` | Inspect dead-lettered proofs and prepare a manual replay |
| `scripts/ops/load_test_inference.py` | Load-test `/inference` with signed requests |
| `scripts/zkml/build_proving_bundles.py` | Build EZKL proving bundles for the seeded models |
| `scripts/zkml/ezkl_mlp_benchmark.py` | Benchmark EZKL on the four tiny fixtures |
| `scripts/zkml/ezkl_scale_benchmark.py` | Benchmark EZKL on an MLP of a chosen width |
| `scripts/zkml/check_zkml_timing_regression.py` | Compare the recorded EZKL numbers with the baseline (CI) |
| `scripts/fl/fl_crash_harness.py` | Slow training run, for crash-recovery tests |

## Common problems

- **`ezkl` will not install on Linux arm64.** There is no wheel. Build the pipeline image with
  `INSTALL_ZKML=true` on a linux/amd64 host, or use `ZKML_BACKEND=development`.
- **A service refuses to start with a chain id error.** `WEB3_CHAIN_ID` does not match the RPC.
- **`/inference` returns 503 `auth_unavailable`.** No chain is configured, so the worker cannot be
  checked. Set `WEB3_RPC_URL` and the TaskManager address.
- **Encrypted models cannot be opened after a restart.** `TEE_SEALED_KEY_PATH` is unset, so each
  process generated its own key. Set the path to the same file in every service.
- **0G uploads stop with `ConfigurationError`.** The wallet holds less than `ZEROG_MIN_BALANCE`.
  Fund it, or lower the minimum.

## Contributing

- Every change needs unit tests. A change that implements a design property needs a Hypothesis
  property test in `tests/property/`.
- `ruff` and `mypy` must pass.
- No secrets in code, tests or docs. Test keys must be marked as test-only.
- Keep docs in the five files under `docs/`. Do not add new status or report files; update
  `docs/STATUS.md` instead.

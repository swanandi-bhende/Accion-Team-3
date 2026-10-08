# Local Development Setup Guide

## Prerequisites
- Python 3.11+ (Flower 1.39 requires 3.11 or newer)
- Docker 20.10+ (and docker-compose)
- Git

## Clone and Navigate
```bash
git clone <repository_url>
cd Accion-Verifiable-AI-Execution-Subsystem
```

## Install Dependencies
The provided `setup.sh` script automates dependency installation:
```bash
./setup.sh
```
*Alternatively, manual installation:*
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

## Configure Environment
Copy the example environment template:
```bash
cp .env.example .env
```
*Note:* The default values in `.env.example` are safe for local development. Real credentials, blockchain RPCs, and Team A/C endpoints must be provided later once integration is unblocked.

### Required environment keys

`common/config.py` is the single source of truth for configuration. Every key
below **has no default** — `Settings()` (and therefore `get_settings()`) raises a
pydantic `ValidationError` if any of them is missing, so `.env` must define all
of them before the Inference Engine starts.

| Key | Purpose |
| --- | --- |
| `TEE_MODE` | Attestation mode, e.g. `sgx` (simulated locally) |
| `TEE_SEALED_KEY_PATH` | Path to the sealed TEE key material |
| `REDIS_URL` | Redis connection string, e.g. `redis://localhost:6379` |
| `MODEL_STORAGE_BACKEND` | `ipfs` or `zerog` |
| `MODEL_STORAGE_URL` | Storage node URL, e.g. `http://localhost:5001` |
| `WEB3_WS_URL` | WebSocket RPC used by the event listener |
| `WEB3_RPC_URL` | HTTP RPC used by the proof submitter |
| `TASK_MANAGER_ADDRESS` | Team A `TaskManager` contract address |
| `ESCROW_ADDRESS` | Team A `Escrow` contract address |
| `PROOF_SUBMITTER_PRIVATE_KEY` | Key that signs proof submissions |
| `PROOF_SUBMITTER_CONTRACT_ADDRESS` | On-chain settlement contract address |
| `ZKML_PROVING_KEY_STORE` | Directory holding zkML proving keys |
| `ZKML_SUPPORTED_ARCHITECTURES` | Comma-separated architectures with circuits |

Optional keys with working defaults: `TEE_INFERENCE_TIMEOUT_S` (30),
`MODEL_MAX_SIZE_MB` (500), `PROOF_BATCH_WINDOW_S` (10), `ZEROG_RPC_ENDPOINT`,
`ZEROG_PRIVATE_KEY`.

Read directly from the environment (not via `Settings`):
`TEE_TRUSTED_KEYS_PATH` — a JSON map of `signer_id` to hex-encoded Ed25519
public key, used by the Verification API as its TEE trust anchor. Without it the
service still verifies zkML proofs but rejects every TEE attestation as
`invalid_signature`.

### Where each value comes from

The keys fall into four groups: values you can write today, values the mock
deployment produces, values only another team can supply, and placeholders that
are validated but not yet read.

**1. Safe local values (usable now)**

```ini
TEE_MODE=sgx
TEE_SEALED_KEY_PATH=./data/sealed_key.bin
REDIS_URL=redis://localhost:6379
MODEL_STORAGE_BACKEND=ipfs
MODEL_STORAGE_URL=http://localhost:5001
WEB3_WS_URL=ws://localhost:8545
WEB3_RPC_URL=http://localhost:8545
ZKML_PROVING_KEY_STORE=./data/proving_keys
ZKML_SUPPORTED_ARCHITECTURES=linear,cnn,mlp,transformer_small
```

`ZKML_SUPPORTED_ARCHITECTURES` must be a subset of
`common/constants.py:SUPPORTED_ARCHITECTURES`. For `PROOF_SUBMITTER_PRIVATE_KEY`
against the local chain you can use hardhat's well-known development account #0,
`0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80` — that key is
public and must **never** be used on a real network.

**2. Produced by the mock deployment**

`TASK_MANAGER_ADDRESS`, `ESCROW_ADDRESS` and `PROOF_SUBMITTER_CONTRACT_ADDRESS`
are emitted by `mocks/contracts/scripts/deploy_and_trigger.js`, which deploys the
stub contracts and **writes all three to `.env.mock`** in the repo root. The mock
`TaskManager` already exposes `submitProofs` and `proof_submitter/runner.py`
loads that ABI, so locally the settlement address is the **same address as
`TASK_MANAGER_ADDRESS`**.

`event-listener` and `proof-submitter` read that file through `env_file` (`.env`
first, then `.env.mock`, so the mock values win). `env_file` is read at container
**creation** time, so the consumers must be created after the deploy has run:

```bash
docker compose up -d redis ipfs hardhat-node
docker compose up hardhat-deploy          # deploys stubs, writes .env.mock
docker compose up -d                      # creates the consumers with those addresses
# if they already existed:
docker compose up -d --force-recreate event-listener proof-submitter
```

The deploy command sets `WEB3_RPC_URL=http://hardhat-node:8545` and the script
waits for that RPC before deploying: inside the deploy container `127.0.0.1` is
that container itself, not the node.

Both consumers also mount `./mocks` read-only. `load_contract_abi` reads the
compiled artifacts under `mocks/contracts/artifacts/`, which are git-ignored build
output produced by that same deploy — the images ship no ABIs of their own.

**3. Only another team can supply**

| Key | Source |
| --- | --- |
| `TASK_MANAGER_ADDRESS`, `ESCROW_ADDRESS` | Team A's deployed contracts (use the mock values above for local work) |
| `PROOF_SUBMITTER_CONTRACT_ADDRESS` | Team 3a's settlement contract exposing `submitProofs` — see `INTEGRATION_CONTRACTS.md` |
| `WEB3_RPC_URL`, `WEB3_WS_URL` | The target chain's HTTP and WebSocket RPC endpoints |

`WEB3_WS_URL` is accepted but not used as a socket today: web3 8.x ships only an
*asynchronous* persistent `WebSocketProvider`, which the synchronous `Web3` client
rejects. The listener logs a warning and polls the HTTP twin of that URL instead
(`WEB3_RPC_URL` when set, otherwise `ws://` rewritten to `http://`), which is
equivalent here because it polls with filters rather than subscribing. Real
subscriptions would need an `AsyncWeb3` client.
| `PROOF_SUBMITTER_PRIVATE_KEY` | A funded key on the target network |
| `TEE_SEALED_KEY_PATH` | The real sealed key from hardware/TEE provisioning |
| `TEE_TRUSTED_KEYS_PATH` | Public keys of the signers whose attestations you accept |
| `ZEROG_*` | 0G Storage endpoints and credentials, if that backend is chosen |

`TEE_TRUSTED_KEYS_PATH` is hand-authored: a JSON object mapping `signer_id` to
the hex-encoded raw Ed25519 public key, for example
`{"<signer_id>": "<64 hex characters>"}`. For a simulated enclave, `signer_id`
is the first 32 hex characters of that same public key.

**4. Validated but not yet read by the code**

These must be *set* (`Settings` requires them) but nothing consumes them yet, so
do not chase real credentials:

- `TEE_SEALED_KEY_PATH` — nothing opens this file; `InferenceEngine` uses a
  built-in default key (`inference/engine.py`).
- `MODEL_STORAGE_BACKEND` — `inference/app.py` always constructs `IPFSBackend`
  regardless of value, and the `zerog` backend raises `NotImplementedError`.
- `ZKML_PROVING_KEY_STORE`, `ZKML_SUPPORTED_ARCHITECTURES` — not read outside
  config; `data/proving_keys/` is currently empty.
- `ZEROG_RPC_ENDPOINT`, `ZEROG_PRIVATE_KEY` — only meaningful once the 0G backend
  is implemented.

### Mock stack overrides: `.env.mock`

`.env.mock` is **generated, not authored**: the deploy script writes
`TASK_MANAGER_ADDRESS`, `ESCROW_ADDRESS` and `PROOF_SUBMITTER_CONTRACT_ADDRESS`
there. It is git-ignored and **optional** — docker-compose loads it with
`required: false`, and it is listed *after* `.env` in `env_file`, so the mock
chain overrides whatever `.env` says while a fresh clone without it still boots.
Add `PROOF_SUBMITTER_PRIVATE_KEY` yourself if you want the submitter to sign with
a specific account.

Because `.env.mock` is read at container **creation** time, recreate the
consumers after the mock chain first writes it:

```bash
docker compose up -d --force-recreate event-listener proof-submitter
```

## Verify Installation
Run tests to ensure dependencies and environment are sane:
```bash
pytest tests/unit/ --tb=short
pytest tests/property/ --tb=short
```

## Run the Application
Start the infrastructure and services:
```bash
docker-compose up -d
```
Verify the APIs are healthy:
- Inference Engine: `GET http://localhost:8000/health`
- Verification API: `GET http://localhost:8001/health`

### Containerised services and ports

Each Python service has its own Dockerfile and an explicit entrypoint, so the
image never depends on a shell default:

| Compose service | Dockerfile | Port | Entrypoint |
| --- | --- | --- | --- |
| `inference-engine` | `inference/Dockerfile` | 8000 | `inference.app:app` |
| `verification-api` | `verification/Dockerfile` | 8001 | `verification.app:create_default_app --factory` |
| `event-listener` | `event_listener/Dockerfile` | — | long-running listener |
| `proof-submitter` | `proof_submitter/Dockerfile` | — | long-running submitter |
| `fl-orchestrator` | `federated_learning/Dockerfile` | — | `python -m federated_learning.runner` |

Infrastructure: `redis` (6379), `ipfs` (5001 API, 8080 gateway),
`hardhat-node` (8545). The `verification-api` and `fl-orchestrator` both point at
the compose-internal `http://ipfs:5001`; the Verification API is intentionally
**not** given blockchain credentials, so it boots without the
proof-submitter configuration.

Run the Verification API outside Docker exactly as documented:
```bash
uvicorn "verification.app:create_default_app" --factory --port 8001
```

## Service Startup Order

Compose declares nine services. Dependencies (`depends_on`) make the real order:
`redis` + `ipfs` → `hardhat-node` → `hardhat-deploy` →
`inference-engine` / `event-listener` / `proof-submitter` / `verification-api` /
`fl-orchestrator`.

There is no seed service: the Model Registry is populated by the Inference
Engine at runtime. To pre-seed a model for manual testing, run
`python scripts/seed_model_registry.py` against the same `REDIS_URL` and
`MODEL_STORAGE_URL` the Engine uses.

## Troubleshooting
- **Redis connection failed:** Ensure `docker-compose up -d redis` is running and port 6379 is available.
- **TEE Unavailable:** Ensure `TEE_MODE=sgx` is set in `.env` for simulation during local dev.
- **`ValidationError` on startup:** the Inference Engine is the only service that
  calls `get_settings()`, so it is the one that refuses to boot on an incomplete
  `.env`; compare against the required-keys table above.
- **Proof submitter exits immediately:** `ConfigurationError: Missing required
  configuration ... proof_submitter_contract_address`. Run the mock deploy so
  `.env.mock` carries the address, then
  `docker compose up -d --force-recreate proof-submitter` (see above).
- **`ZKML_PROVING_KEY_STORE` looks empty:** expected — `data/proving_keys/` is not
  populated and nothing reads the value yet (see “Validated but not yet read”).
  zkML proofs are generated by the development-commitment backend until EZKL is
  wired in; see `ZKML_SPIKE.md`.

# Local Development Setup Guide

## Prerequisites
- Python 3.11+ (Flower 1.39 requires 3.11 or newer)
- Docker 20.10+ (and docker-compose)
- Node 20+ — the local chain (`chain/`) runs Team A's deployed contracts, and the chain tests start it
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
pip install -r requirements/base.txt
pip install -r requirements/dev.txt
```

## Configure Environment
Copy the example environment template:
```bash
cp .env.example .env
```
*Note:* The default values in `.env.example` are safe for local development. Real credentials, blockchain RPCs, and Team A/C endpoints must be provided later once integration is unblocked.

### Required environment keys

`common/config.py` is the single source of truth for configuration. `Settings`
supplies defaults for most keys. Each service then calls
`get_settings().require(...)` for the keys it uses, and refuses to start with one
error that lists every missing key. The table lists the keys that must be set
for the services that use them.

| Key | Purpose |
| --- | --- |
| `TEE_MODE` | Attestation mode, e.g. `sgx` (simulated locally) |
| `REDIS_URL` | Redis connection string, e.g. `redis://localhost:6379` |
| `MODEL_STORAGE_URL` | Storage node URL, e.g. `http://localhost:5001` |
| `WEB3_WS_URL` | WebSocket RPC used by the event listener |
| `WEB3_RPC_URL` | HTTP RPC used by the proof submitter |
| `WEB3_CHAIN_ID` | Expected chain id (`16602` on Team A's Galileo testnet); a mismatch stops both on-chain services |
| `TASK_MANAGER_ADDRESS` | Team A `TaskManager` contract address |
| `ESCROW_ADDRESS` | Team A `EscrowContract` address |
| `PROOF_SUBMITTER_PRIVATE_KEY` | Key that signs proof submissions |
| `PROOF_SUBMITTER_CONTRACT_ADDRESS` | Contract exposing `submitProofs` (Team A's `TaskManager`) |
| `ZKML_PROVING_KEY_STORE` | Directory holding zkML proving keys |

Optional keys with working defaults: `TEE_INFERENCE_TIMEOUT_S` (30),
`PROOF_BATCH_WINDOW_S` (10).
`TEE_SEALED_KEY` (blank) is the 256-bit key that opens sealed models. Blank gives
each engine its own random key, so a sealed model only runs in the process that
sealed it. Services that share a sealed model must share this value.

Model uploads are durable. `ModelRegistry.upload_model` writes the binary to
`data/upload_spool/` and queues it in Redis before the first attempt. It retries
with 5 s to 300 s backoff for as long as storage is unreachable (Requirement 7.5),
and a restart finishes queued uploads through `resume_uploads()`.

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
REDIS_URL=redis://localhost:6379
MODEL_STORAGE_URL=http://localhost:5001
WEB3_WS_URL=ws://localhost:8545
WEB3_RPC_URL=http://localhost:8545
ZKML_PROVING_KEY_STORE=./data/proving_keys
```

Supported zkML architectures are fixed in `common/constants.py:SUPPORTED_ARCHITECTURES`.
For `PROOF_SUBMITTER_PRIVATE_KEY`
against the local chain you can use hardhat's well-known development account #0,
`0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80` — that key is
public and must **never** be used on a real network.

**2. Supplied by Team A (already committed)**

`TASK_MANAGER_ADDRESS`, `ESCROW_ADDRESS`, `PROOF_SUBMITTER_CONTRACT_ADDRESS`,
`SETTLEMENT_ADDRESS`, `BOUNTY_TOKEN_ADDRESS`, `WEB3_CHAIN_ID=16602`, the artifact
basenames and `CONTRACT_ARTIFACTS_DIR=team_a_assets` are all recorded in
`deployments/galileo.json` — Team A's real 0G Galileo deployment. `.env.example`
carries those values, so there is nothing to generate before the first boot.

The local stack runs **Team A's own contracts**, not stubs: the `hardhat-node`
service starts `chain/` with `chainId: 16602`, and the one-shot
`team-a-contracts` service runs `python -m scripts.ops.install_team_a_local`,
which replays their deployed runtime bytecode onto that node at the same Galileo
addresses and reproduces the constructor state (owner, escrow token, wiring,
Prover allow-list). The listener and submitter therefore enforce Team A's real
authorisation and lifecycle rules.

```bash
docker compose up -d redis ipfs hardhat-node
docker compose up team-a-contracts         # installs + wires Team A's contracts
docker compose up -d
```

`team-a-contracts` sets `WEB3_RPC_URL=http://hardhat-node:8545`: inside a
container `127.0.0.1` is that container itself, not the node.

Both consumers mount `./team_a_assets` and `./deployments` read-only, so
`load_contract_abi` reads Team A's exported ABIs at startup. The images ship no
ABIs of their own.

Our earlier stub contracts are archived in `mocks/legacy_mock_contracts/` and
**must not be used**; see `mocks/README.md`.

**3. Only another team can supply**

| Key | Source |
| --- | --- |
| `TASK_MANAGER_ADDRESS`, `ESCROW_ADDRESS` | Team A's deployed contracts — `deployments/galileo.json` has them |
| `PROOF_SUBMITTER_CONTRACT_ADDRESS` | Team A's `TaskManager` (`submitProofs` lives there) — see `INTEGRATION_CONTRACTS.md` |
| `TASK_MANAGER_ARTIFACT`, `ESCROW_ARTIFACT`, `PROOF_SUBMITTER_ARTIFACT` | Artifact basenames, when the provider names a contract differently (`EscrowContract`, not `Escrow`) |
| `WEB3_RPC_URL`, `WEB3_WS_URL` | The target chain's HTTP and WebSocket RPC endpoints |

`WEB3_WS_URL` is accepted but not used as a socket today: web3 8.x ships only an
*asynchronous* persistent `WebSocketProvider`, which the synchronous `Web3` client
rejects. The listener logs a warning and polls the HTTP twin of that URL instead
(`WEB3_RPC_URL` when set, otherwise `ws://` rewritten to `http://`), which is
equivalent here because it polls with filters rather than subscribing. Real
subscriptions would need an `AsyncWeb3` client.
| `PROOF_SUBMITTER_PRIVATE_KEY` | A funded key on the target network |
| `TEE_TRUSTED_KEYS_PATH` | Public keys of the signers whose attestations you accept |

`TEE_TRUSTED_KEYS_PATH` is hand-authored: a JSON object mapping `signer_id` to
the hex-encoded raw Ed25519 public key, for example
`{"<signer_id>": "<64 hex characters>"}`. For a simulated enclave, `signer_id`
is the first 32 hex characters of that same public key.

**4. Simulator behaviour**

- `ZKML_PROVING_KEY_STORE` — the prover creates this directory, but the
  development backend does not load keys from it yet.
- The simulated enclave has no provisioned key. `InferenceEngine` generates a
  fresh key per instance unless one is passed in (`inference/engine.py`).
- There is no 0G storage backend. Only IPFS is implemented.

### Team A addresses are read, not copied

`MOCK_DEPLOYMENTS_PATH=./deployments/galileo.json` tells `Settings` to adopt any
contract address that is blank or still the all-zero placeholder from
`deployments/galileo.json`. Compose sets it for the listener and submitter, and
the file is committed, so a fresh clone boots without editing `.env`.

The name is historical (it used to point at the mock deploy's output); the file
it points at now holds Team A's deployment. Add `PROOF_SUBMITTER_PRIVATE_KEY`
yourself if you want the submitter to sign with a specific account.

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
| `pipeline` | `docker/pipeline.Dockerfile` | — | long-running listener feeding inference, proving and settlement |
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

Dependencies (`depends_on`) make the real order: `redis` + `ipfs` → `hardhat-node`
→ `team-a-contracts` → `inference-engine` / `pipeline` / `proof-submitter` /
`verification-api` / `fl-orchestrator`.

The `seed-model` one-shot job registers the toy models (`toy_linear`, `toy_mlp`) and
the model for the default task's description hash (`linear-model`) when the stack starts. The pipeline keys each model by its task's description hash, so a
task settles only when a model is registered under that hash. Other tasks end as
`dropped_model_not_found`.

## Troubleshooting
- **Redis connection failed:** Ensure `docker-compose up -d redis` is running and port 6379 is available.
- **TEE Unavailable:** Ensure `TEE_MODE=sgx` is set in `.env` for simulation during local dev.
- **`ValidationError` on startup:** the Inference Engine is the only service that
  calls `get_settings()`, so it is the one that refuses to boot on an incomplete
  `.env`; compare against the required-keys table above.
- **Proof submitter exits immediately:** `ConfigurationError: Missing required
  configuration ... proof_submitter_contract_address` or `... task_manager_address`.
  Check that `MOCK_DEPLOYMENTS_PATH` points at the committed
  `deployments/galileo.json` (compose sets it for the listener and submitter). If
  it runs outside compose, export the addresses or set the path yourself.
- **`ZKML_PROVING_KEY_STORE` looks empty:** expected — `data/proving_keys/` is not
  populated and nothing reads the value yet (see “Validated but not yet read”).
  zkML proofs are generated by the development-commitment backend until EZKL is
  wired in; see `ZKML_SPIKE.md`.

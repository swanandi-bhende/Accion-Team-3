# Deployment

How to bring the Verifiable AI Execution subsystem up with Docker Compose, check
that it is healthy, and tear it down. Run every command from the repository root.

## Prerequisites

- Docker Engine and Docker Compose v2 (`docker compose version`). The stack was
  tested with Docker 29.7 and Compose v5.5. The service images use
  `additional_contexts` to build on the shared base image, so an older Compose may
  need upgrading.
- An x86_64 host. Some service images pull platform-specific wheels at build
  time; see [zkML builds](#zkml-builds).
- At least 4 GB free. The stack runs Redis, a Kubo IPFS node, a Hardhat node,
  and several custom Python images.

## Clean start

From a fresh clone:

```bash
cp .env.example .env              # the defaults already target the local mock stack
docker compose up --build -d
scripts/ops/smoke_test_compose.sh
```

`up --build` builds the shared base image (`docker/base.Dockerfile`) first, then the
service images that build on it. It also deploys the mock contracts and seeds the
model registry, so no step is done by hand. The first build is the slowest because
it installs the Python dependencies and the Hardhat packages; later runs reuse the
build cache unless you pass `--build`.

To follow logs:

```bash
docker compose logs -f inference-engine
docker compose logs -f event-listener
docker compose logs -f proof-submitter
docker compose logs -f verification-api
docker compose logs -f fl-orchestrator
docker compose logs -f            # everything at once, useful for startup ordering
```

## Verifying the stack

`scripts/ops/smoke_test_compose.sh` checks the running stack in five steps and
stops at the first failure:

1. **Services.** Every long-running service reports `healthy`, and the one-shot
   jobs (`team-a-contracts`, `seed-model`, `fl-orchestrator`) exited 0. The script
   waits up to `SMOKE_WAIT_TIMEOUT` seconds (default 180).
2. **Redis.** `PING` answers `PONG`.
3. **Chain.** The Hardhat node answers JSON-RPC with Team A's chain id, the
   contracts recorded in `deployments/galileo.json` have code on chain, and the
   event-listener's environment holds those same addresses.
4. **Inference.** The inference engine serves the seeded `toy_linear` model and
   returns an execution receipt.
5. **Dispatch.** A task is created and funded on Team A's contracts
   (`python -m scripts.ops.trigger_team_a_task`), and the running event-listener
   records it as dispatched within `SMOKE_TASK_TIMEOUT` seconds (default 60). Set
   `SMOKE_SKIP_TRIGGER=1` to skip this step.

A healthy run ends with `== smoke_test_compose: end ==`. Every check prints one
line:

```
SERVICE=redis STATUS=succeeded ping=PONG
SERVICE=contracts STATUS=succeeded TASK_MANAGER_ADDRESS=0x... code=present listener=matches
SERVICE=inference-engine STATUS=succeeded model=toy_linear inference=receipt
SERVICE=event-listener STATUS=succeeded task_id=0x... dispatched=true
```

Any `STATUS=failed` line names the service and the reason. The most common causes
are listed under [Troubleshooting](#troubleshooting).

The worker processes (event-listener, proof-submitter) expose no ports. They
declare healthchecks, so their dependents wait on real readiness.

## Ports

| Service          | Host port | Notes                                |
| ---------------- | --------- | ------------------------------------ |
| Redis            | 6379      | Persisted in the `redis_data` volume |
| IPFS (Kubo)      | 5001      | API; gateway on host port `IPFS_GATEWAY_PORT` (default 8080) |
| Hardhat node     | 8545      | JSON-RPC, chainId 31337              |
| Inference engine | 8000      | `POST /inference`, `GET /health`     |
| Verification API | 8001      | `POST /verify`, `GET /health`        |

## Configuration

`common/config.py` is the only place that reads environment variables. Each
service calls `get_settings().require(...)` with exactly the variables it needs,
so a federated-learning worker never sees blockchain settings and the inference
engine never sees proof-submission keys.

`.env.example` documents every variable. Each one is tagged `MOCK`, `LOCAL`, `REAL`,
or `NOT-READ-YET`, so you can see at a glance which values stand in for another
team's deployment. The compose file overrides some values inside containers (for
example `WEB3_RPC_URL` points at the `hardhat-node` service), so the stack works
without editing `.env`.

## Local chain: Team A's contracts

The `team-a-contracts` service runs once per boot. It does **not** compile or
deploy a stand-in: Team A ship ABIs and deployed addresses, not source, so
`scripts/ops/install_team_a_local.py` replays their deployed runtime bytecode
(`team_a_assets/bytecode.json`) onto the local node with `hardhat_setCode` at the
same Galileo addresses, then restores the constructor state — owners, the escrow
token (an immutable baked into the bytecode), the escrow↔TaskManager↔Settlement
wiring, and the Prover allow-list. The node in `chain/` runs `chainId: 16602`, so
the same environment profile works locally and on Galileo.

The listener and submitter read the addresses from the committed
`deployments/galileo.json` (via `MOCK_DEPLOYMENTS_PATH`, a historical name), so
nothing is generated and a fresh boot needs no manual copies. Create and fund a
task yourself with:

```bash
python -m scripts.ops.trigger_team_a_task
```

## Team A's Galileo deployment

The contract interface is no longer a mock assumption. Team A deployed to the 0G
Galileo testnet (chain id **16602**) and shipped bare ABI exports in
`team_a_assets/`; `deployments/galileo.json` records the addresses in the names
`common/config.py` reads. See [INTEGRATION_CONTRACTS.md](INTEGRATION_CONTRACTS.md)
for the confirmed signatures and [INTEGRATION_LOG.md](INTEGRATION_LOG.md) for open
drift.

Nothing is copied into the image: the services read the same directories you can
inspect, so the handoff stays auditable. The default local profile already runs
Team A's contracts; to reach the real testnet instead, point the same profile at
their RPC:

```bash
# 1. Fill WEB3_RPC_URL/WEB3_WS_URL from Team A and export the profile.
set -a; . deployments/team-a.env.example; set +a

# 2. Supply the funded signer key through a secure channel (never in the repo).
export PROOF_SUBMITTER_PRIVATE_KEY=...

# 3. Run the two on-chain services against it.
python -m event_listener.runner
python -m proof_submitter.runner
```

The differences from the default local profile are all configuration:

| Variable | Local (`chain/`) | Galileo |
| --- | --- | --- |
| `WEB3_RPC_URL` / `WEB3_WS_URL` | `hardhat-node:8545` | Team A's endpoint (not yet published) |
| `WEB3_CHAIN_ID` | `16602` (same as Galileo) | `16602` |
| `CONTRACT_ARTIFACTS_DIR` | `team_a_assets` | `team_a_assets` |
| `ESCROW_ARTIFACT` | `EscrowContract` | `EscrowContract` |
| Addresses | `deployments/galileo.json` (bytecode replayed locally) | `deployments/galileo.json` |

Three guards make a misconfigured switch loud instead of silent:

- **Chain guard.** With `WEB3_CHAIN_ID` set, both services compare it with the RPC's
  `eth_chainId` and exit on a mismatch, so Team A's addresses are never polled on a
  local node.
- **Prover allow-list.** `TaskManager.submitProofs` requires `isProver(signer)`. The
  submitter checks it at startup and exits with the address and the exact
  `setProver` call to make. Team A's record has `PROVER: null`, so this fires until
  they allow-list the signer.
- **Batch sizing.** `submitProofs` is bounded by `MAX_PROOF_BATCH`. The submitter
  reads that limit and splits larger buffers, and it dead-letters only the entries
  the contract rejects (`ProofRejected`) instead of resending accepted ones.

`deployments/team-a.env.example` deliberately leaves the RPC URLs and the private
key blank: they are the two things Team A has not delivered.

## Development overrides

`docker-compose.override.yml` is loaded automatically next to
`docker-compose.yml`. It is for development and does three things:

- **Live source.** Mounts `common/`, `inference/`, `verification/` and
  `proof_generator/` read-only into the API containers and runs uvicorn with
  `--reload`, so Python edits take effect without a rebuild.
- **Workers are not mounted.** The event-listener and proof-submitter are
  long-running loops, and a bind-mounted module under a running loop gives
  surprising results. Rebuild and recreate them instead:

  ```bash
  docker compose build event-listener
  docker compose up -d --force-recreate event-listener
  ```

  The same applies to `proof-submitter`. Because the base image is shared, only
  the changed service rebuilds.
- **Seeding.** `seed-model` runs when its container is created, which means the
  first `up` and the `up` after `down`, in both modes. To re-seed on demand (for
  example after wiping Redis), run it as a one-off:

  ```bash
  docker compose run --rm seed-model
  ```

To prove the exact artifact a reviewer would build, run without the override.
Mounted source reflects your local tree, not the tree the image was built from:

```bash
mv docker-compose.override.yml docker-compose.override.yml.bak
docker compose down -v
docker compose up --build -d
scripts/ops/smoke_test_compose.sh
mv docker-compose.override.yml.bak docker-compose.override.yml
```

The smoke test checks health and behaviour. It does not check whether `--reload`
is active.

## Stopping and resetting

```bash
docker compose down        # stop; the workers get 30 s to finish in-flight work
docker compose down -v     # also wipe Redis and IPFS volumes (clean registry and block state)
```

On SIGTERM the event-listener finishes its current poll pass and persists its block
progress, so it resumes from the last block on the next start. The proof-submitter
flushes its pending batch before it exits.

Clean rebuild, when images and volumes both need to go:

```bash
docker compose down -v --remove-orphans
docker compose up --build -d
scripts/ops/smoke_test_compose.sh
```

`--remove-orphans` removes services that were deleted from the compose file.

To force a fresh install of Team A's contracts, reset the node's state. The
installer is idempotent and checks the bytecode on every boot, so nothing is
cached on disk:

```bash
docker compose down -v
docker compose up --build -d
```

## Limits of the containerised stack

The compose stack runs each component as its own service, but it does not yet run
the whole path in one container:

- **Inference, proof and settlement are not chained.** The event-listener dispatches
  tasks, and the inference engine and proof-submitter run as separate services. The
  composed inference → proof → settlement path runs in-process through
  `python -m inference.pipeline`, which is not a compose service yet.
- **fl-orchestrator is a one-shot job.** It trains the configured rounds, stores the
  model, and exits 0. The long-running Flower entrypoint is
  `federated_learning/network_runner.py`, and it is not part of this stack.
- **The TEE is simulated and the proof backend is the development commitment.** See
  `docs/TEE_NOTES.md` and `docs/ZKML_SPIKE.md`.

## Troubleshooting

**`.env` is missing or stale.** Services read `.env` if it exists, but the defaults
in `.env.example` are what the stack was tested with. Start from a fresh copy with
`cp .env.example .env`. Blockchain and storage variables in an old `.env` can
point services at the wrong endpoints.

**Stale volumes from a previous run.** Redis and IPFS keep state across `down`. If
you see old tasks, models, or provenance, or the listener resumes from an old block
after you redeployed the chain, reset with `docker compose down -v` and then `up`.

**A host port is already in use** (8545, 6379, 5001, 8000, 8001, or the IPFS
gateway). Stop the other process. For the IPFS gateway, set `IPFS_GATEWAY_PORT` in
`.env` or the shell instead. The other ports are fixed in `docker-compose.yml`.

**First boot is slow, or services stay `starting`.** On the first `up`,
`hardhat-node` runs `npm install` before Hardhat starts. Wait, or raise the wait:

```bash
SMOKE_WAIT_TIMEOUT=600 scripts/ops/smoke_test_compose.sh
```

**A service is unhealthy.** Run `docker compose ps` and confirm its dependencies
report `healthy`. The event-listener and proof-submitter healthchecks depend on
Redis and, for the proof-submitter, the chain.

**The smoke test says `dispatched` never appeared.** Check that the install ran:
`docker compose logs team-a-contracts` should end with
`TaskManager.isProver(owner)=True`, and the addresses in
`deployments/galileo.json` should have code on chain. Then check the listener log
for the task id the smoke test printed.

**`PROOF_SUBMISSION_FAILED_PERMANENTLY` in proof-submitter logs.** The submitter
retries three times, then dead-letters the batch to the Redis list
`proof_submitter:dlq`. Inspect it with:

```bash
docker compose exec redis redis-cli LRANGE proof_submitter:dlq 0 -1
```

To replay an entry, use `scripts/ops/replay_dlq.py`.

### zkML builds

`requirements/zkml.txt` (EZKL and ONNX) is not installed in any service image by
default. EZKL publishes no linux/arm64 wheel, and the runtime proof path is pure
Python. To opt in on x86_64, set `INSTALL_ZKML: "true"` for `proof-submitter` in
`docker-compose.yml`. The build fails on Apple Silicon.

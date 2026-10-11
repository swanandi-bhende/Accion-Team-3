# Deployment

How to bring the Verifiable AI Execution subsystem up with Docker Compose, check
that it is healthy, and tear it down. Run every command from the repository root.

## Prerequisites

- Docker Engine 24+ and Compose v2.20+ (`docker compose version`).
- An x86_64 host. Some service images pull platform-specific wheels at build
  time; see [zkML builds](#zkml-builds).
- At least 4 GB free. The stack runs Redis, a Kubo IPFS node, a Hardhat node,
  and several custom Python images.

No other setup is needed: `docker compose up` builds the images, deploys the mock
contracts, seeds the model registry, and leaves every service running.

## Quick start

```bash
docker compose up --build -d
scripts/ops/smoke_test_compose.sh
```

The first `up --build` is the slowest because each image installs its Python
dependencies. Later runs reuse cached images unless you pass `--build`.

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

`scripts/ops/smoke_test_compose.sh` checks the `/health` endpoint of each FastAPI
service and confirms the mock chain answers JSON-RPC. A healthy run prints one
line per check:

```
SERVICE=inference-engine STATUS=succeeded endpoint=http://127.0.0.1:8000/health status=200
SERVICE=verification-api STATUS=succeeded endpoint=http://127.0.0.1:8001/health status=200
SERVICE=hardhat-node STATUS=succeeded chain_id=31337
```

Any `STATUS=failed` line means the stack is not ready yet. The script waits up to
40 seconds for the RPC. On a slow first boot, raise the wait:

```bash
ROCKSDB_WAIT_TIMEOUT=90 scripts/ops/smoke_test_compose.sh
```

The worker processes (event-listener, proof-submitter) expose no ports. They
declare compose healthchecks, so dependents wait on real readiness.

## Ports

| Service          | Host port | Notes                                  |
| ---------------- | --------- | -------------------------------------- |
| Redis            | 6379      | Persisted in the `redis_data` volume   |
| IPFS (Kubo)      | 5001      | API; gateway on 8080                   |
| Hardhat node     | 8545      | JSON-RPC, chainId 31337                |
| Inference engine | 8000      | `POST /inference`, `GET /health`       |
| Verification API | 8001      | `POST /verify`, `GET /health`          |

## Configuration

```bash
cp .env.example .env
```

`common/config.py` is the only place that reads environment variables. Each
service calls `get_settings().require(...)` with exactly the variables it needs,
so a federated-learning worker never sees blockchain settings and the inference
engine never sees proof-submission keys. `.env.example` documents every variable
and marks the ones that point at the mock chain. The compose stack works without
editing `.env`.

## Mock chain

The `hardhat-deploy` service runs once per boot. It deploys the stub contracts
from `mocks/contracts/contracts/` and publishes their addresses in two places, so
services that start before the deploy finishes can still find them:

1. `.env.mock` at the repo root, read by the listener and submitter through their
   `env_file` entry.
2. `mocks/contracts/deployments/localhost.json`, read by `Settings` when
   `MOCK_DEPLOYMENTS_PATH` is set (compose sets it).

Resetting the chain changes the addresses. `docker compose down -v` followed by
`up` re-runs the deploy and rewrites both files.

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
- **Debug profile.** `seed-model` is also gated behind the `debug-only` profile,
  so you can re-seed the registry on demand (for example after wiping Redis):

  ```bash
  docker compose --profile debug-only up -d seed-model
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

The smoke test checks health and RPC reachability in both modes. It does not
check whether `--reload` is active.

## Stopping and resetting

```bash
docker compose down        # stop; workers finish in-flight work
docker compose down -v     # also wipe Redis and IPFS volumes (clean registry and block state)
```

Clean rebuild, when images and volumes both need to go:

```bash
docker compose down -v --remove-orphans
docker compose up --build -d
scripts/ops/smoke_test_compose.sh
```

`--remove-orphans` removes services that were deleted from the compose file.

To force a fresh Hardhat compile and deploy, remove the git-ignored build output.
The next `up` regenerates it:

```bash
rm -rf mocks/contracts/artifacts mocks/contracts/deployments .env.mock
docker compose down -v
docker compose up --build -d
```

## Troubleshooting

**A host port is already in use** (8545, 6379, 8000, 8001). Stop the other process,
or change the host port in `docker-compose.yml` and in the smoke-test URL.

**First boot is slow, or the smoke test reports `network_unreachable`.** On the
first `up`, `hardhat-node` runs `npm install` inside the mounted tree before
Hardhat starts. Wait, or retry with `ROCKSDB_WAIT_TIMEOUT` raised.

**`event-listener` or `proof-submitter` stays unhealthy.** Both healthchecks
depend on Redis and the chain. Run `docker compose ps` and confirm the
dependencies report `healthy`.

**The listener is healthy but nothing is dispatched.** Check that the deploy ran:
`mocks/contracts/deployments/localhost.json` should hold non-empty addresses, and
`docker compose logs hardhat-deploy` should show `Deploying mock contracts` and
`Wrote addresses`.

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

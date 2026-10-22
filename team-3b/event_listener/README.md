# Event Listener

**Purpose:** Monitors Team 3a's blockchain for `TaskCreated` and `TaskFunded` events via WebSocket, extracting task parameters and dispatching inference workloads. Maintains block persistence to prevent duplicate processing.

**Requirements Mapped:**
- Requirement 5: Blockchain Event Listening

## Local chain run

Start Redis and the node with `docker compose up -d redis hardhat-node` (the node
in `chain/` runs `chainId: 16602`), then install Team A's contracts onto it with
`python -m scripts.ops.install_team_a_local`, and create a task with
`python -m scripts.ops.trigger_team_a_task`. Set `WEB3_WS_URL`, `WEB3_RPC_URL`,
`WEB3_CHAIN_ID=16602`, `TASK_MANAGER_ADDRESS`, and `ESCROW_ADDRESS` — or point
`MOCK_DEPLOYMENTS_PATH` at `deployments/galileo.json` and let `Settings` fill in
the addresses. The listener loads Team A's exported ABIs from `team_a_assets/`,
persists progress under `event_listener:last_block`, and exposes its status
through the `create_status_app()` helper at `/listener/status`.

The stub contracts the listener used to run against are archived in
`mocks/legacy_mock_contracts/` and must not be used.

The implementation accepts both `fromBlock`/`toBlock` and current web3.py
`from_block`/`to_block` filter keyword conventions. Pending task state is also
stored in Redis so a restart can resume a funded-before-created task without
dispatching it twice.

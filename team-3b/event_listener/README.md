# Event Listener

**Purpose:** Monitors Team 3a's blockchain for `TaskCreated` and `TaskFunded` events via WebSocket, extracting task parameters and dispatching inference workloads. Maintains block persistence to prevent duplicate processing.

**Requirements Mapped:**
- Requirement 5: Blockchain Event Listening

## Local mock-chain run

Start Redis and Hardhat with `docker-compose up -d redis hardhat-node` and set
`WEB3_WS_URL`, `TASK_MANAGER_ADDRESS`, and `ESCROW_ADDRESS`. The listener loads
the generated Hardhat ABIs from `mocks/contracts/artifacts/`, persists progress
under `event_listener:last_block`, and exposes its status through the
`create_status_app()` helper at `/listener/status`.

The implementation accepts both `fromBlock`/`toBlock` and current web3.py
`from_block`/`to_block` filter keyword conventions. Pending task state is also
stored in Redis so a restart can resume a funded-before-created task without
dispatching it twice.

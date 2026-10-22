# Integration status (living record)

At-a-glance state of every cross-team dependency. This is the file Step 18.2 asks
for: one place a reviewer can check instead of inferring readiness from code.

Detail lives elsewhere and is linked, never duplicated:

- Interface shapes and confirmed signatures: [INTEGRATION_CONTRACTS.md](INTEGRATION_CONTRACTS.md)
- Every discrepancy, who raised it, and its status: [INTEGRATION_LOG.md](INTEGRATION_LOG.md)
- Checkpoint evidence: [CHECKPOINTS.md](CHECKPOINTS.md)

Last updated: **2026-10-21**.

## Readiness signal

A dependency is **READY** only when its owner has done all three:

1. published the artifact in a durable place (tagged release, or a committed file
   that names the tag), not just a chat message;
2. named the version or commit the artifact corresponds to;
3. confirmed the counterpart can reach it (endpoint reachable, key funded, or
   payload accepted by the consumer).

Team 3b records the date and the artifact path in the table below in the same change
that starts integrating against it. Anything short of that stays **PARTIAL** or
**NOT READY**, and the gap is listed under "Remaining".

## Status

| # | Dependency | Owner | Status | Artifact / evidence | Blocking fully? |
| --- | --- | --- | --- | --- | --- |
| 1 | TaskManager / Escrow / Settlement ABIs + addresses | Team A | **READY** (2026-10-20) | `team_a_assets/*.abi.json`, `deployments/galileo.json` | No |
| 2 | Deployed bytecode for local rehearsal | Team A | **READY** (2026-10-20) | `team_a_assets/bytecode.json`, replayed by `scripts/ops/install_team_a_local.py` | No |
| 3 | Galileo RPC endpoints (HTTP + WS, rate limits, gas assumptions) | Team A | **NOT READY** | `deployments/team-a.env.example` leaves both blank | **Yes** — blocks 18.5 on testnet |
| 4 | Funded signer on Galileo + `setProver(signer, true)` | Team A | **NOT READY** | `deployments/galileo.json` has `PROVER: null` | **Yes** — blocks any real submission |
| 5 | Finality policy (confirmations, block time, reorg expectations) | Team A | **NOT READY** | open question 3 in INTEGRATION_CONTRACTS.md | No for local, yes for production |
| 6 | Bid/worker policy for tasks our prover serves | Team A | **NOT READY** | DRIFT-011: `settleTask` needs `taskWorker`; nobody has accepted a bid for us | **Yes** — blocks settlement on testnet |
| 7 | Change policy (versioning, named owner, channel) | Team A | **NOT READY** | INTEGRATION_CONTRACTS.md question 4 | No, but it is the readiness signal itself |
| 8 | `InferenceRequest` payload (exact fields, encoding, `task_id` format) | Team C | **PARTIAL** — schema documented, no real traffic | DRIFT-003 (`input_data` base64 vs raw) still open | **Yes** — blocks 18.6 |
| 9 | Response expectations (sync / polling / webhook, timeout budget) | Team C | **NOT READY** | DRIFT-012: our response does not echo `task_id` | **Yes** |
| 10 | Model id → registry mapping | Team C | **NOT READY** | `toy_*` models are placeholders | **Yes** |
| 11 | Authentication to `POST /inference` | Team C + 3b | **NOT READY** — none implemented on either side | — | No for staging, yes for production |
| 12 | Team C staging URL and trigger owner | Team C | **NOT READY** | — | Blocks the scheduled cross-team job |
| 13 | Our endpoints and schemas, handed to Team C | 3b | **READY** (2026-10-20) | `docs/INTEGRATION_CONTRACTS.md` §Team C | No |
| 14 | Config-driven ABI/address swap | 3b | **READY** (2026-10-20) | `CONTRACT_ARTIFACTS_DIR`, `*_ARTIFACT`, `MOCK_DEPLOYMENTS_PATH` | No |

## What we can prove today, and against what

| Check | Runs against | Command | Status |
| --- | --- | --- | --- |
| Contract rules and lifecycle (create, fund, bid, accept, prove, settle) | Team A's deployed bytecode, local `chain/` | `pytest tests/integration/test_team_a_lifecycle.py` | Passing |
| Listener reads Team A's events | Same | `pytest tests/integration/test_event_listener_team_a_chain.py` | Passing |
| Submitter sends a real batched `submitProofs` | Same | `pytest tests/integration/test_proof_submitter_team_a_chain.py` | Passing |
| Team A deployment preflight | Local, or Galileo when an endpoint exists | `python -m scripts.ops.team_a_preflight --rpc-url ... --chain-id 16602` | Passing locally |
| Team C payload + receipt conformance | Captured payload file, optional live service | `python -m scripts.ops.team_c_conformance payload.json --endpoint ...` | Tool ready; no real payload yet |
| Three-team path | Local compose stack (Team C's leg replayed) | `python -m scripts.ops.cross_team_e2e --inference-url ... --redis-url ...` | See CHECKPOINTS.md |
| Same, on Galileo | Blocked | Needs rows 3 and 4 | **Cannot run** |

## Remaining from Team A

1. **RPC endpoints** — HTTP and WebSocket, with rate limits and any gas assumptions
   (row 3). Without this, 18.5 cannot run against the testnet at all.
2. **A funded signer and `setProver(signer, true)`** (row 4). The submitter refuses to
   start otherwise, by design. The key must arrive through a secure channel.
3. **Who accepts the bid** (row 6). `submitProofs` does not require a bid, but
   `SettlementContract.settleTask` reverts `NoWorkerAssigned` without one, so the
   bounty for proofs we submit is unpayable until a worker is assigned. Confirm
   whether the prover should also be the worker, or a third party bids.
4. **Finality policy** (row 5): confirmations before an event is final, testnet block
   time, and whether `TaskFunded` can precede `TaskCreated` in log order.
5. **Change policy** (row 7): versioning, announcement channel, named owner.

## Remaining from Team C

1. **The real request payload** (row 8): exact fields, and the `input_data` encoding
   (base64 as documented, or raw bytes as the service currently behaves — DRIFT-003).
2. **Response contract** (row 9): synchronous, polling, or webhook; acceptable
   timeout; and whether they need `task_id` echoed (DRIFT-012 — it is not today).
3. **Model ids** (row 10) and their mapping to registry entries.
4. **Authentication** (row 11) for `POST /inference`.
5. **Three to five real payloads with expected responses**, and their error cases.
6. **How their frontend reads results** — no results API exists on our side.
7. **A staging URL and a named person to trigger the cross-team run** (row 12).

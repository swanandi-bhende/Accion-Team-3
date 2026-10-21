# Integration log

Every discrepancy between the documented interface assumptions
(`INTEGRATION_CONTRACTS.md`) and what the code, the mocks, or a team's deliverable
actually does. Each entry is recorded before any code changes to resolve it, so the
history shows what changed and why.

Statuses: **open** (not yet discussed with the owner), **raised** (sent to the
owner, awaiting a decision), **resolved** (fixed on the agreed side), **won't fix
(workaround)** (accepted with a documented workaround).

Entries pinned by a strict `xfail` test start passing when the drift is resolved;
the marker is then removed in the same change, together with the entry's status.

## Open entries

### DRIFT-003: `input_data` is not decoded from base64

- **Documented:** `InferenceRequest.input_data` is `base64_encoded_bytes`.
- **Observed:** `common/models.py` types it as `bytes`, and pydantic stores the JSON string's UTF-8 bytes unchanged. `POST /inference` therefore runs inference on the literal base64 text. `scripts/ops/smoke_test_compose.sh` sends the base64 text as-is, and `scripts/ops/load_test_inference.py` base64-encodes its payload, so both exercise the literal base64 text.
- **Impact:** any real client following the documented encoding gets outputs computed over the wrong input. This is a Team C contract question first, and our bug second.
- **Pinned by:** `tests/unit/test_inference_contract.py::test_input_data_is_decoded_from_base64`.
- **Raised with:** not yet. Confirm the encoding with Team C (checklist item 1). Fixing it changes the receipts the smoke test checks.
- **Status:** open.

### DRIFT-006: Team A's Prover allow-list is empty

- **Documented:** the handoff asks whether the proof-submitter address must be whitelisted and how that is granted.
- **Observed:** `TaskManager.isProver(address)` gates `submitProofs` and reverts `NotProver`; Team A's deployment record has `PROVER: null`. Their `setProver(address, bool)` is owner-only.
- **Impact:** every submission reverts until the signer is allow-listed. Without a check this looks like a healthy service quietly filling the dead-letter queue.
- **Resolved on our side:** the submitter reads `isProver(signer)` at startup and refuses to start with a message naming the address and the `setProver` call (`CHECK_PROVER_ALLOWLIST`).
- **Raised with:** Team A, 2026-10-20 — need the funded signer address allow-listed before the staging test.
- **Status:** raised (blocked on Team A).

### DRIFT-007: no Galileo RPC endpoints published

- **Documented:** the handoff asks for HTTP and WebSocket RPC endpoints, rate limits and gas assumptions.
- **Observed:** the handoff names the network (0G Galileo, chain id 16602) and the addresses, but no endpoint. `deployments/team-a.env.example` leaves `WEB3_RPC_URL`/`WEB3_WS_URL` blank.
- **Impact:** the services cannot connect to the real chain; only the local mock is reachable.
- **Resolved on our side:** `WEB3_CHAIN_ID` is enforced at startup, so a stack configured for Galileo cannot silently run against a local Hardhat node.
- **Raised with:** Team A, 2026-10-20.
- **Status:** raised (blocked on Team A).

### DRIFT-009: funding is ERC-20, the mock funded with native value

- **Documented:** the handoff asks whether the bounty is native or ERC-20.
- **Observed:** `EscrowContract.deposit(taskId, amount)` pulls an ERC-20 (`TEST`) balance; the mock's `fundTask(taskId)` is `payable` and uses `msg.value`. The `TaskFunded` event shape is identical either way.
- **Impact:** none for the listener or submitter, which only read the amount. Any task-funding script or Team C agent must `approve` first on the real chain.
- **Raised with:** Team A, 2026-10-20 — confirmed ERC-20. Team C must use `approve` + `deposit`.
- **Status:** raised (informational for us, blocking for whoever funds tasks).

### DRIFT-011: settlement needs an accepted bid, and our pipeline never makes one

- **Documented:** `submitProofs` and settlement were treated as one flow: proof recorded, bounty paid.
- **Observed:** on Team A's real contracts `submitProofs` is *not* gated on the bid flow — it is accepted on an open or unfunded task and moves it to status 2. `SettlementContract.settleTask` is the step that pays, and it reverts `NoWorkerAssigned()` unless the creator has called `acceptBid`, which sets `taskWorker(taskId)`. Probe results are in `docs/INTEGRATION_CONTRACTS.md` §6 and pinned by `tests/integration/test_team_a_lifecycle.py`.
- **Impact:** proofs we submit on a task nobody bid on are recorded but the escrowed bounty can never be released. The gap is invisible to the listener and submitter, which have no bid role at all.
- **Resolved on our side:** none yet — this is a scope question, not a bug in the submitter. Tests now assert the real rule instead of assuming the mock's behaviour.
- **Raised with:** Team A, 2026-10-21 — who accepts the bid for tasks our prover serves, and should the prover be the worker (bid + accept) rather than a third party?
- **Status:** raised (blocked on Team A).

## Resolved entries

### DRIFT-001: `taskId` is `bytes32`, not `uint256`

- **Documented:** `TaskCreated.taskId` and `TaskFunded.taskId` were documented as `uint256 indexed`; `submitProofs.taskIds` as `uint256[]`.
- **Observed:** Team A's ABIs use `bytes32` / `bytes32[]` everywhere; our mock already did.
- **Impact:** none in code — the listener keys on the hex form and the submitter hashes to `bytes32` — but the docs would have misled any client building task ids.
- **Resolved:** docs now state `bytes32`; the three strict-xfail tests were replaced with positive assertions against Team A's ABI (`tests/unit/test_contract_interface.py`).
- **Status:** resolved (2026-10-20).

### DRIFT-002: event field names

- **Documented:** `TaskCreated` had `bounty`; `TaskFunded` had `amount`.
- **Observed:** `TaskCreated` uses `bountyAmount` (both our mock and Team A), and `TaskFunded` previously used `fundedAmount` in our mock; Team A uses `amount`.
- **Impact:** the listener builds the task payload from these names. A mismatch drops the funded amount or raises a `KeyError` inside the poll loop.
- **Resolved:** the listener reads `amount` and falls back to `fundedAmount`, the mock now emits `amount`, and a parity test asserts the mock's `TaskFunded` shape equals Team A's.
- **Status:** resolved (2026-10-20).

### DRIFT-004: `submitProofs` return value and partial success

- **Documented:** `submitProofs` returned nothing; revert behaviour was unstated; our retry logic assumed all-or-nothing.
- **Observed:** it returns `uint256 accepted`, reverts `ArrayLengthMismatch` and `BatchTooLarge` (bounded by `MAX_PROOF_BATCH`), and rejects individual entries with `ProofRejected(taskId, reason)` instead of reverting the batch.
- **Impact:** a retry after a partial batch would resubmit already-accepted proofs, and an oversized buffer would revert forever until dead-lettered.
- **Resolved:** the submitter chunks by `MAX_PROOF_BATCH`, decodes `ProofRejected`, dead-letters only the rejected proofs, and counts the accepted ones. The mock now returns a count, enforces the limit and emits `ProofSubmitted`.
- **Status:** resolved (2026-10-20).

### DRIFT-005: `submitProofs` is on `TaskManager`, not on a separate settlement contract

- **Documented:** submission was assumed to go to a Team 3a settlement contract while Team A's `TaskManager` only emitted task events.
- **Observed:** `TaskManager` owns `submitProofs`, `ProofSubmitted` and `ProofRejected`; `SettlementContract.settleTask(taskId)` is a later, separate lifecycle step with its own `TaskSettled` event.
- **Impact:** pointing `PROOF_SUBMITTER_CONTRACT_ADDRESS` at `SettlementContract` would fail at the first batch, because it has no `submitProofs`.
- **Resolved:** docs and `deployments/galileo.json` set it to `TaskManager`; the ABI is chosen by `PROOF_SUBMITTER_ARTIFACT` rather than hard-coded.
- **Status:** resolved (2026-10-20).

### DRIFT-008: the submitter assumed an unlocked node account

- **Documented:** nothing; the code called `web3.eth.accounts[0]`.
- **Observed:** a real RPC has no unlocked accounts, so building a transaction from `accounts[0]` raises `IndexError` before signing. The private key was only used to sign, never to derive the sender or the nonce.
- **Impact:** the submitter could not have run against Galileo at all.
- **Resolved:** the sender is derived from `PROOF_SUBMITTER_PRIVATE_KEY` (falling back to `accounts[0]` for the mock), and is used for `from`, `nonce` and the allow-list check.
- **Status:** resolved (2026-10-20).

### DRIFT-010: our mock contracts hid Team A's authorisation and lifecycle rules

- **Documented:** `INTEGRATION_CONTRACTS.md` described Team A's interface, and `mocks/contracts/` reproduced it closely enough that the local stack and the test suite ran against the stubs.
- **Observed:** the stubs emitted the same events but enforced nothing: no `NotProver`, no `NotTaskCreator`, no `NoWorkerAssigned`, no status transitions, no ERC-20 escrow accounting. Every chain test was green while `submitProofs` against the real flow was untested.
- **Impact:** exactly the drift this log exists to catch went undetected locally, including DRIFT-009 and DRIFT-011.
- **Resolved:** the stubs are archived in `mocks/legacy_mock_contracts/` and marked do-not-use. The local node (`chain/`) has no Solidity at all: `scripts/ops/install_team_a_local.py` replays Team A's deployed runtime bytecode at their Galileo addresses, and the compose stack, smoke test and test suite all install and exercise that. Filenames follow: `tests/integration/test_team_a_*.py`.
- **Status:** resolved (2026-10-21).

## Team status

| Team | Last contact | Deliverable received | Owner (name / handle) | Channel |
| --- | --- | --- | --- | --- |
| Team A | 2026-10-20 | Galileo deployment + ABIs (`team_a_assets/`), addresses in `deployments/galileo.json` | to be filled in | to be filled in |
| Team C | none recorded | none | to be filled in | to be filled in |

# Integration Contracts & Interfaces

> [!IMPORTANT]
> Team A (smart contracts) has delivered a deployment and ABIs: the interface below
> is **real**, not an assumption. Team C (agent logic) has not delivered, so its
> section is still marked MOCK. Drift between any assumption and reality is recorded
> in [INTEGRATION_LOG.md](INTEGRATION_LOG.md).

## Integration status

| Dependency | Status | Last change | Notes |
| --- | --- | --- | --- |
| Team A: TaskManager / EscrowContract | **READY FOR INTEGRATION** | 2026-10-20 | Deployed on 0G Galileo testnet (16602); ABIs committed in `team_a_assets/`, addresses in `deployments/galileo.json`. |
| Team A: `submitProofs` | **READY FOR INTEGRATION** | 2026-10-20 | `submitProofs` lives on `TaskManager`, returns `uint256 accepted`, is bounded by `MAX_PROOF_BATCH`, and requires an allow-listed Prover. |
| Team A: SettlementContract | **READY (not called by us)** | 2026-10-21 | `settleTask(taskId)` refuses a task without an accepted bid (`NoWorkerAssigned`) and with no completed task (`TaskNotCompleted`). Our submitter records proofs but never takes part in the bid flow, so settlement of our tasks needs a worker to be assigned by someone else. Exercised in `tests/integration/test_team_a_lifecycle.py`. |
| Team C: agent (`InferenceRequest` sender) | MOCKED | 2026-10-12 | Payload is the schema in section 3 below. No real traffic received. |
| Team 3b: config-driven ABI and address swap | **READY** | 2026-10-20 | `CONTRACT_ARTIFACTS_DIR` + the three `*_ARTIFACT` names select the ABI set; verified against both the Hardhat mock tree and Team A's bare ABI exports. |

Readiness signal: a dependency moves to **READY FOR INTEGRATION** when its owner
posts the deployment in the shared channel and pushes the artifacts (ABI JSON,
address list, network config) as a tagged release, or as a commit to a
`docs/`-level handoff file that names the tag. Team 3b updates this table in the
same change that starts the integration work.

## Team A: Smart Contracts (delivered)

Network: **0G Galileo testnet, chain id 16602**. Source of the values below:
`team_a_assets/team_a.txt` and the ABIs in `team_a_assets/`.

| Contract | Address |
| --- | --- |
| Test ERC-20 (`TEST`, 18 decimals) | `0x22374BC6FF12FF2Bf407Da332F16Ee65D52CE0EC` |
| `TaskManager` | `0xA55f34e4137aD55f48aB268d4361F2225C4B5078` |
| `EscrowContract` | `0x5B685Ca03A8072FB3f9721Dd7017Ed0D8C719217` |
| `SettlementContract` | `0x45D1a7AF404D2EDf5E5c23C7c0b6338227b9BC4E` |
| `StakingContract` | `0x41eCc7485d2Bbc188B4F638871ca3f00D2c65C38` |
| `IdentityContract` | `0xC70c8865eACC340AB15Af8d4e726F438CD639Db9` |

`deployments/galileo.json` records the same deployment under the key names
`common/config.py` adopts, plus `PROVER: null` (see the allow-list note below).

### 1. `TaskCreated` — `TaskManager`

```solidity
event TaskCreated(
    bytes32 indexed taskId,
    address indexed creator,
    bytes32 descriptionHash,
    uint256 deadline,
    uint256 bountyAmount
);
```

`taskId` is `bytes32`, so the listener keys task state on its hex form.

### 2. `TaskFunded` — `EscrowContract`

```solidity
event TaskFunded(
    bytes32 indexed taskId,
    address indexed funder,
    uint256 amount
);
```

The field is `amount`. Our earlier mock called it `fundedAmount`, so the listener
accepts either name and the mock now emits `amount` too.

Funding is **ERC-20**, not native value: fund a task with
`approve(TEST, amount)` then `EscrowContract.deposit(taskId, amount)`.

### 3. `submitProofs` — `TaskManager`

```solidity
function submitProofs(
    bytes32[] calldata taskIds,
    bytes32[] calldata proofHashes,
    string[] calldata modelCids,
    bytes32[] calldata resultHashes
) external returns (uint256 accepted);

uint256 public constant MAX_PROOF_BATCH;   // read it, do not hard-code it
```

Semantics we code against:

- Reverts `ArrayLengthMismatch` when the four arrays differ in length.
- Reverts `BatchTooLarge` when the batch exceeds `MAX_PROOF_BATCH`, so the submitter
  splits a buffer into contract-sized chunks.
- A well-formed batch does **not** revert for individual failures: entries the
  contract accepts emit `ProofSubmitted`, entries it refuses emit
  `ProofRejected(taskId, reason)`, and the return value is the accepted count.
  The submitter therefore dead-letters only the rejected proofs and never resends
  the accepted ones.
- Only an allow-listed Prover may call it (`NotProver` otherwise).

### 4. Proof and settlement events — `TaskManager` / `SettlementContract`

```solidity
event ProofSubmitted(bytes32 indexed taskId, bytes32 proofHash, string modelCid, bytes32 resultHash, address indexed submitter);
event ProofRejected(bytes32 indexed taskId, uint8 reason);
event TaskSettled(bytes32 indexed taskId, address indexed worker, uint256 workerAmount, uint256 stakerAmount, uint256 treasuryAmount);
```

### 5. Prover allow-list

```solidity
mapping(address => bool) public isProver;
function setProver(address prover, bool allowed) external;   // owner only
```

Team A's deployment record has `Prover: null`, so the proof submitter's signer must
be added with `setProver(<signer>, true)` before any submission can succeed. The
submitter refuses to start while `isProver(signer) == false`
(`CHECK_PROVER_ALLOWLIST=true`); this turns a revert-into-DLQ loop into one
actionable line in the log.

### 6. Task lifecycle (observed on Team A's deployed bytecode)

`getStatus(taskId)` returns `ITaskManager.TaskStatus`: `0` open, `1` worker
assigned, `2` completed, `3` settled. Funding does **not** move the status; the
escrow records the deposit and emits `TaskFunded` on its own contract.

```
createTask            → 0 open
placeBid(worker)      → 0      (BidPlaced; allowed while open)
acceptBid(creator)    → 1      (BidAccepted; sets taskWorker)
markComplete(creator)→ 2      (creator only; NotTaskCreator otherwise)
submitProofs(prover)  → 2      (allowed at 0 or 1 too — not gated on the bid flow)
settleTask(anyone)    → 3      (requires taskWorker; NoWorkerAssigned otherwise)
```

Two consequences matter for us:

- **`submitProofs` does not require a bid.** A proof on an open, even unfunded,
  task is accepted. Our submitter therefore works on tasks where no worker was
  ever assigned.
- **Settlement does.** `SettlementContract.settleTask` pays `taskWorker` and
  reverts `NoWorkerAssigned` when none was accepted. The bounty for proofs we
  submit stays in escrow until the creator accepts a bid and the task reaches
  status 2. Assigning the worker is not our step, but nothing will settle without
  it.

### Configuration for the Galileo profile

`deployments/team-a.env.example` is a ready-made profile. The variables that matter:

```dotenv
WEB3_RPC_URL=            # not published by Team A yet
WEB3_WS_URL=             # not published by Team A yet
WEB3_CHAIN_ID=16602
CONTRACT_ARTIFACTS_DIR=team_a_assets
TASK_MANAGER_ARTIFACT=TaskManager
ESCROW_ARTIFACT=EscrowContract
PROOF_SUBMITTER_ARTIFACT=TaskManager
TASK_MANAGER_ADDRESS=0xA55f34e4137aD55f48aB268d4361F2225C4B5078
ESCROW_ADDRESS=0x5B685Ca03A8072FB3f9721Dd7017Ed0D8C719217
PROOF_SUBMITTER_CONTRACT_ADDRESS=0xA55f34e4137aD55f48aB268d4361F2225C4B5078
BOUNTY_TOKEN_ADDRESS=0x22374BC6FF12FF2Bf407Da332F16Ee65D52CE0EC
CHECK_PROVER_ALLOWLIST=true
```

`WEB3_CHAIN_ID` is enforced: both on-chain services compare it with the RPC's
`eth_chainId` and refuse to start on a mismatch.

### What is still outstanding from Team A

1. **RPC endpoints** (HTTP and WebSocket) and any rate limits or gas assumptions.
2. **A funded signer** on Galileo, plus `setProver` for its address. The key must
   arrive through a secure channel, never in the repository.
3. **Finality policy**: confirmations before an event counts as final, and the
   testnet block time.
4. **Change policy**: how breaking changes are announced and versioned, plus a named
   owner and channel.
5. **Deadline and settlement semantics**: `TaskFunded` may precede `TaskCreated` in
   the log order (the listener already buffers it), and a task does **not** need
   `BidAccepted` before `submitProofs` — but settlement does need an assigned
   worker. Confirm this is intended, and who accepts bids for tasks our prover
   serves.

### Local chain

The local stack runs Team A's contracts themselves, not a stand-in. Team A ship
ABIs (`team_a_assets/*.abi.json`) and deployed addresses
(`deployments/galileo.json`), not source, so
`scripts/ops/install_team_a_local.py` replays their deployed runtime bytecode
(`team_a_assets/bytecode.json`) onto the `chain/` node with `hardhat_setCode` at
the same Galileo addresses, then restores the constructor state and wiring. The
node runs `chainId: 16602`, so this local profile and the real testnet differ only
in the RPC endpoint.

That means the authorisation and lifecycle rules above are enforced in tests too:
`submitProofs` from a signer that is not on the Prover allow-list reverts, and
tasks are funded through Team A's ERC-20 escrow `deposit` rather than a payable
stub.

Our own stub contracts are archived in `mocks/legacy_mock_contracts/` and are
**retired — do not use them**. They matched the ABI shape but none of the rules,
and tests against them hid exactly the drift this document records.

## Team C: Agent Interface (MOCK)

### `InferenceRequest` Payload
Expected payload pushed by Team C's agent when triggering execution (or resolved from decentralized storage via the `descriptionHash`).

```json
{
  "model_id": "string",
  "input_data": "base64 of a .npy file holding a float32 tensor of shape (rows, features)",
  "task_id": "string (optional)"
}
```

`input_data` is base64 text. The service decodes it to the `.npy` bytes of a NumPy
array, which is the model's input tensor. Pickle is never read. DRIFT-003 (base64
was not decoded) is resolved, and DRIFT-015 records the format as a proposal that
Team C has not yet agreed.

The response is an `ExecutionReceipt`. Its `output` field is the base64 of the
model's output as a `.npy` file, and `output_hash` is the SHA-256 of those bytes.

### Model format

A registered model is an ONNX file with one float32 input and one float32 output.
Architectures that can run today are `linear` and `mlp`. `cnn` and
`transformer_small` are accepted by the zkML path but cannot run until a model of
that shape is registered and tested.

### Handoff checklist for Team C

1. **Exact `InferenceRequest` payload**: required fields, the encoding of `input_data` (base64 as documented, or something else), the format of `task_id`, and the `model_id` naming scheme. *Required.*
2. **Response expectations**: synchronous HTTP response, polling, or webhook. Their acceptable timeout, which must cover the 30 s TEE budget plus the proof stage. *Required.*
3. **Model mapping**: which `model_id` values they will request, how each maps to a Model Registry entry, and each model's architecture (`linear`, `cnn`, `mlp`, `transformer_small`). *Required.*
4. **Task creation path**: whether their agent calls `TaskManager.createTask` directly, and whether it signs anything. *Required.*
5. **`descriptionHash` derivation**: what it hashes, so the on-chain `descriptionHash` can be mapped to a `model_id`. *Required.*
6. **Authentication**: how their agent authenticates to `POST /inference`. The service currently has none.
7. **Real sample traffic**: three to five real request payloads with their expected responses, and the error cases they handle.
8. **Results access**: what their frontend queries to show results (an endpoint, on-chain events, or a shared store). The service does not expose a results API yet.
9. **Staging**: their staging URL, availability window, and who triggers the cross-team test.

### What Team 3b provides in return

- The endpoints and payload schemas this service exposes (`POST /inference`, `POST /verify`, health routes).
- The event and function assumptions in this document, with drift tracked in INTEGRATION_LOG.md.

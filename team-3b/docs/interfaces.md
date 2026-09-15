# Integration Interfaces

**Changelog:**
- **2023-10-25 (Team 3b):** Initial draft defining exact schemas expected from Team A and Team C based on design doc.

> [!WARNING]
> These interfaces are currently mocked. Any change to Team A/C interfaces must be logged here and flagged in the next cross-team sync.

## Team A: Smart Contracts (Mocked)

### 1. `TaskCreated` Event
Emitted by `TaskManager` contract.

```solidity
event TaskCreated(
    bytes32 indexed taskId,           // ASSUMPTION: bytes32 is used for task IDs rather than uint256
    address indexed creator,          // The wallet address of the task creator
    bytes32 descriptionHash,          // ASSUMPTION: This is an IPFS CID or raw hash pointing to task metadata. Need to confirm with Team A.
    uint256 deadline,                 // Unix timestamp for task deadline
    uint256 bountyAmount              // Wei-denominated bounty amount
);
```

### 2. `TaskFunded` Event
Emitted by `Escrow` contract.

```solidity
event TaskFunded(
    bytes32 indexed taskId,           // Matches taskId from TaskCreated
    address indexed funder,           // The wallet address providing the funds
    uint256 fundedAmount              // Wei-denominated funding amount
);
```

### 3. `submitProofs` Function
Called by Proof Submitter onto Team 3a's settlement contract.

```solidity
function submitProofs(
    bytes32[] calldata taskIds,
    bytes32[] calldata proofHashes,
    string[] calldata modelCids,
    bytes32[] calldata resultHashes
) external;
```
**Assumptions & Questions for Team A:**
- **ASSUMPTION:** Arrays must be of equal length.
- **QUESTION:** Does the whole batch revert on partial failure, or can individual entries fail independently? (Our DLQ logic depends on this).

---

## Team C: Agent Interface (Mocked)

### `InferenceRequest` Payload
Expected payload pushed by Team C's agent to the Inference Engine boundary.

```json
{
  "model_id": "string",
  "input_data": "base64_encoded_bytes",
  "task_id": "string" 
}
```
**Assumptions for Team C:**
- **ASSUMPTION:** `task_id` correlates directly with the `taskId` emitted in `TaskCreated`.
- **ASSUMPTION:** The agent receives the execution receipt asynchronously. The exact delivery mechanism (polling vs webhook) is pending Team C's frontend architecture details.

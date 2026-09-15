# Integration Contracts & Interfaces (MOCK)

> [!WARNING]
> Both Team A (Smart Contracts) and Team C (Agent Logic) have not delivered finalized interfaces yet. This document defines the exact mock schemas Team 3b is coding against. When the real interfaces drop, diff against this file to detect drift.

## Team A: Smart Contracts

### 1. `TaskCreated` Event
Expected from `TaskManager` contract over WebSocket.

```solidity
event TaskCreated(
    uint256 indexed taskId,
    address indexed creator,
    bytes32 descriptionHash, // Maps to model_id
    uint256 deadline,
    uint256 bounty
);
```

### 2. `TaskFunded` Event
Expected from `Escrow` contract over WebSocket.

```solidity
event TaskFunded(
    uint256 indexed taskId,
    address indexed funder,
    uint256 amount
);
```

### 3. `submitProofs` Function
Called by Proof Submitter onto Team 3a's settlement contract.

```solidity
function submitProofs(
    uint256[] calldata taskIds,
    bytes32[] calldata proofHashes,
    string[] calldata modelCids,
    bytes32[] calldata resultHashes
) external;
```

## Team C: Agent Interface

### `InferenceRequest` Payload
Expected payload pushed by Team C's agent when triggering execution (or resolved from decentralized storage via the `descriptionHash`).

```json
{
  "model_id": "string",
  "input_data": "base64_encoded_bytes",
  "task_id": "string (optional)"
}
```

## Local Mock Environment
Stub implementations of these contracts are located in `/mocks/contracts/`. You can trigger them locally using Hardhat to simulate an active chain.

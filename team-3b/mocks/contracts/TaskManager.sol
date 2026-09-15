// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

/**
 * @title TaskManager Stub
 * @dev Mock contract for local development of the Event Listener
 */
contract TaskManager {
    event TaskCreated(
        uint256 indexed taskId,
        address indexed creator,
        bytes32 descriptionHash,
        uint256 deadline,
        uint256 bounty
    );

    uint256 public nextTaskId = 1;

    // Trigger for local testing
    function emitTaskCreated(bytes32 descriptionHash, uint256 deadline, uint256 bounty) external {
        emit TaskCreated(nextTaskId, msg.sender, descriptionHash, deadline, bounty);
        nextTaskId++;
    }

    // Mock settlement function
    function submitProofs(
        uint256[] calldata taskIds,
        bytes32[] calldata proofHashes,
        string[] calldata modelCids,
        bytes32[] calldata resultHashes
    ) external {
        // Validation logic stub
    }
}

// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

contract TaskManager {
    event TaskCreated(
        bytes32 indexed taskId,
        address indexed creator,
        bytes32 descriptionHash,
        uint256 deadline,
        uint256 bountyAmount
    );

    uint256 private _taskIdCounter = 1;

    // Generates a mock taskId for testing
    function createTask(bytes32 descriptionHash, uint256 deadline, uint256 bountyAmount) external returns (bytes32) {
        bytes32 taskId = bytes32(_taskIdCounter);
        _taskIdCounter++;

        emit TaskCreated(taskId, msg.sender, descriptionHash, deadline, bountyAmount);
        return taskId;
    }

    // Mock settlement function returning receipt
    function submitProofs(
        bytes32[] calldata taskIds,
        bytes32[] calldata proofHashes,
        string[] calldata modelCids,
        bytes32[] calldata resultHashes
    ) external {
        require(taskIds.length == proofHashes.length, "Array length mismatch");
        require(taskIds.length == modelCids.length, "Array length mismatch");
        require(taskIds.length == resultHashes.length, "Array length mismatch");

        // Internally this does nothing but allows ProofSubmitter to get a tx receipt
    }
}

// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

/// Local stand-in for Team A's TaskManager. The event and function shapes mirror
/// the real ABI (`team_a_assets/TaskManager.abi.json`) so services exercise the
/// same code path locally, but the authorisation and task-lifecycle rules are
/// deliberately removed: the mock exists to emit events, not to enforce policy.
contract TaskManager {
    error ArrayLengthMismatch();
    error BatchTooLarge();

    event TaskCreated(
        bytes32 indexed taskId,
        address indexed creator,
        bytes32 descriptionHash,
        uint256 deadline,
        uint256 bountyAmount
    );

    event ProofSubmitted(
        bytes32 indexed taskId,
        bytes32 proofHash,
        string modelCid,
        bytes32 resultHash,
        address indexed submitter
    );

    uint256 public constant MAX_PROOF_BATCH = 50;

    uint256 private _taskIdCounter = 1;

    // Generates a mock taskId for testing
    function createTask(bytes32 descriptionHash, uint256 deadline, uint256 bountyAmount) external returns (bytes32) {
        bytes32 taskId = bytes32(_taskIdCounter);
        _taskIdCounter++;

        emit TaskCreated(taskId, msg.sender, descriptionHash, deadline, bountyAmount);
        return taskId;
    }

    // Same signature as Team A's: returns how many proofs were accepted. The mock
    // accepts every entry of a well-formed, in-limit batch and reverts otherwise.
    function submitProofs(
        bytes32[] calldata taskIds,
        bytes32[] calldata proofHashes,
        string[] calldata modelCids,
        bytes32[] calldata resultHashes
    ) external returns (uint256 accepted) {
        if (
            taskIds.length != proofHashes.length ||
            taskIds.length != modelCids.length ||
            taskIds.length != resultHashes.length
        ) {
            revert ArrayLengthMismatch();
        }
        if (taskIds.length > MAX_PROOF_BATCH) {
            revert BatchTooLarge();
        }

        for (uint256 index = 0; index < taskIds.length; index++) {
            emit ProofSubmitted(
                taskIds[index],
                proofHashes[index],
                modelCids[index],
                resultHashes[index],
                msg.sender
            );
        }
        return taskIds.length;
    }
}

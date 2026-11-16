// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title ISettlement
/// @notice Interface for SettlementContract, implementing Requirement 3
/// (Settlement and Tokenomics Distribution).
interface ISettlement {
    event TaskSettled(
        bytes32 indexed taskId,
        address indexed worker,
        uint256 workerAmount,
        uint256 stakerAmount,
        uint256 treasuryAmount
    );

    error InsufficientFunds();
    error TaskNotCompleted();
    error NoWorkerAssigned();

    /// @notice Distributes a completed task's escrow 81/9/10 and marks the task settled.
    function settle(bytes32 taskId, address worker) external;

    /// @notice Permissionless settlement to the worker assigned via `acceptBid`.
    /// Anyone (a relayer, the worker, Team 3c's agent) may trigger it once the task is Completed.
    function settleTask(bytes32 taskId) external;
}

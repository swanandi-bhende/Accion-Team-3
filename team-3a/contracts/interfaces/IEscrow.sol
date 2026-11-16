// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title IEscrow
/// @notice Interface for the EscrowContract, implementing Requirement 1 (Escrow Fund Management).
interface IEscrow {
    event TaskFunded(bytes32 indexed taskId, address indexed funder, uint256 amount);

    error InvalidAmount();
    error WithdrawalNotAllowed();

    /// @notice Locks `amount` tokens for `taskId`. Reverts InvalidAmount on zero amount.
    function deposit(bytes32 taskId, uint256 amount) external;

    /// @notice Returns the escrowed balance for a given task.
    function getBalance(bytes32 taskId) external view returns (uint256);

    /// @notice Releases `amount` of the task's escrow to `to`. Restricted to SettlementContract.
    function release(bytes32 taskId, address to, uint256 amount) external;
}

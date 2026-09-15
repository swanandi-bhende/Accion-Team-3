// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

/**
 * @title Escrow Stub
 * @dev Mock contract for local development of the Event Listener
 */
contract Escrow {
    event TaskFunded(
        uint256 indexed taskId,
        address indexed funder,
        uint256 amount
    );

    // Trigger for local testing
    function emitTaskFunded(uint256 taskId, uint256 amount) external {
        emit TaskFunded(taskId, msg.sender, amount);
    }
}

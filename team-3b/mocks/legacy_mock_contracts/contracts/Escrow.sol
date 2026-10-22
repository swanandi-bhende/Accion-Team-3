// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

contract Escrow {
    // Field names match Team A's EscrowContract so the listener parses the mock
    // and the Galileo deployment with the same code path.
    event TaskFunded(
        bytes32 indexed taskId,
        address indexed funder,
        uint256 amount
    );

    // Mock function to trigger event
    function fundTask(bytes32 taskId) external payable {
        emit TaskFunded(taskId, msg.sender, msg.value);
    }
}

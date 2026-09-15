// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

contract Escrow {
    event TaskFunded(
        bytes32 indexed taskId,
        address indexed funder,
        uint256 fundedAmount
    );

    // Mock function to trigger event
    function fundTask(bytes32 taskId) external payable {
        emit TaskFunded(taskId, msg.sender, msg.value);
    }
}

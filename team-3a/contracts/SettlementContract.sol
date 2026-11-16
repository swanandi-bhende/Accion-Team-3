// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./interfaces/ISettlement.sol";
import "./interfaces/IEscrow.sol";
import "./interfaces/IStaking.sol";
import "./interfaces/ITaskManager.sol";

/// @title SettlementContract
/// @notice Distributes a completed task's escrowed bounty 81 / 9 / 10 to worker,
/// stakers, and treasury (Requirement 3).
contract SettlementContract is ISettlement {
    uint256 private constant BPS_DENOMINATOR = 10000;
    uint256 private constant STAKER_BPS = 900;
    uint256 private constant TREASURY_BPS = 1000;

    IEscrow public immutable escrow;
    IStaking public immutable staking;
    ITaskManager public immutable taskManager;
    address public immutable treasury;
    address public owner;

    modifier onlyOwner() {
        require(msg.sender == owner, "SettlementContract: caller is not owner");
        _;
    }

    constructor(address _escrow, address _staking, address _taskManager, address _treasury) {
        require(_treasury != address(0), "SettlementContract: zero treasury");
        escrow = IEscrow(_escrow);
        staking = IStaking(_staking);
        taskManager = ITaskManager(_taskManager);
        treasury = _treasury;
        owner = msg.sender;
    }

    /// @inheritdoc ISettlement
    /// @dev The worker share is computed as the remainder rather than as its own
    /// percentage. That is what makes the three parts sum to the escrowed total for
    /// any input, satisfying Requirement 3.5 and Property 7 — floor division on all
    /// three shares would leave up to 2 wei stranded in escrow.
    function settle(bytes32 taskId, address worker) external onlyOwner {
        _settle(taskId, worker);
    }

    /// @inheritdoc ISettlement
    function settleTask(bytes32 taskId) external {
        address worker = taskManager.taskWorker(taskId);
        if (worker == address(0)) revert NoWorkerAssigned();
        _settle(taskId, worker);
    }

    function _settle(bytes32 taskId, address worker) private {
        require(worker != address(0), "SettlementContract: zero worker");

        if (taskManager.getStatus(taskId) != ITaskManager.TaskStatus.Completed) {
            revert TaskNotCompleted();
        }

        uint256 total = escrow.getBalance(taskId);
        if (total == 0) revert InsufficientFunds();
        ITaskManager.Task memory task = taskManager.getTask(taskId);
        if (total < task.bountyAmount) revert InsufficientFunds();

        uint256 stakerAmount = (total * STAKER_BPS) / BPS_DENOMINATOR;
        uint256 treasuryAmount = (total * TREASURY_BPS) / BPS_DENOMINATOR;

        // An empty staking pool would make distributeYield revert and strand the task.
        // Reroute the staker share to the treasury instead; the 81/9/10 split still
        // sums to the total, which is what Property 7 asserts.
        bool poolEmpty = staking.totalStaked() == 0 || stakerAmount == 0;
        if (poolEmpty) {
            treasuryAmount += stakerAmount;
            stakerAmount = 0;
        }

        uint256 workerAmount = total - stakerAmount - treasuryAmount;

        // Mark settled before moving funds — TaskManager rejects a second settlement,
        // so this is the reentrancy guard for the release calls below.
        taskManager.markSettled(taskId);

        escrow.release(taskId, worker, workerAmount);
        if (stakerAmount > 0) {
            escrow.release(taskId, address(staking), stakerAmount);
            staking.distributeYield(stakerAmount);
        }
        escrow.release(taskId, treasury, treasuryAmount);

        emit TaskSettled(taskId, worker, workerAmount, stakerAmount, treasuryAmount);
    }
}

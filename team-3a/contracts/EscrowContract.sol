// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "./interfaces/IEscrow.sol";
import "./interfaces/ITaskManager.sol";

/// @title EscrowContract
/// @notice Holds task bounty funds in escrow until settlement (Requirement 1).
/// @dev Only the SettlementContract may call `release`. Set once, post-deployment,
/// via `setSettlementContract` since Settlement depends on Escrow's address too
/// (circular deploy dependency resolved with a one-time setter).
contract EscrowContract is IEscrow {
    using SafeERC20 for IERC20;

    IERC20 public immutable token;
    address public owner;
    address public settlementContract;
    ITaskManager public taskManager;

    mapping(bytes32 => uint256) public escrowBalances;
    mapping(bytes32 => address) public taskDepositors;

    modifier onlySettlement() {
        require(msg.sender == settlementContract, "EscrowContract: caller is not SettlementContract");
        _;
    }

    constructor(address _token) {
        token = IERC20(_token);
        owner = msg.sender;
    }

    /// @notice One-time wiring of the SettlementContract address (deploy-order dependency).
    function setSettlementContract(address _settlement) external {
        require(msg.sender == owner, "EscrowContract: caller is not owner");
        require(settlementContract == address(0), "EscrowContract: already set");
        settlementContract = _settlement;
    }

    function setTaskManagerContract(address _taskManager) external {
        require(msg.sender == owner, "EscrowContract: caller is not owner");
        require(address(taskManager) == address(0), "EscrowContract: already set");
        taskManager = ITaskManager(_taskManager);
    }

    /// @inheritdoc IEscrow
    function deposit(bytes32 taskId, uint256 amount) external {
        if (amount == 0) revert InvalidAmount();

        ITaskManager.Task memory task = taskManager.getTask(taskId);
        require(task.creator != address(0), "EscrowContract: task does not exist");
        require(task.status != ITaskManager.TaskStatus.Settled, "EscrowContract: task already settled");

        token.safeTransferFrom(msg.sender, address(this), amount);
        escrowBalances[taskId] += amount;

        if (taskDepositors[taskId] == address(0)) {
            taskDepositors[taskId] = msg.sender;
        }

        emit TaskFunded(taskId, msg.sender, amount);
    }

    /// @inheritdoc IEscrow
    function getBalance(bytes32 taskId) external view returns (uint256) {
        return escrowBalances[taskId];
    }

    /// @inheritdoc IEscrow
    /// @dev Requirement 1.3: depositors cannot withdraw their own escrowed funds directly.
    /// Only SettlementContract, via `release`, can move escrowed funds.
    function release(bytes32 taskId, address to, uint256 amount) external onlySettlement {
        require(escrowBalances[taskId] >= amount, "EscrowContract: insufficient escrow balance");
        escrowBalances[taskId] -= amount;
        token.safeTransfer(to, amount);
    }

    /// @notice Explicit rejection path for Requirement 1.3 / Property 2: a depositor
    /// attempting to withdraw their own escrowed funds while the task is active.
    function withdraw(bytes32) external pure {
        revert WithdrawalNotAllowed();
    }
}

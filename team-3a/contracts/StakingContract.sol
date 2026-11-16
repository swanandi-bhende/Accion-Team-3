// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "./interfaces/IStaking.sol";

/// @title StakingContract
/// @notice Stake tokens and earn a proportional share of the 9% staker yield (Requirement 5).
/// @dev Uses the reward-per-token accumulator pattern. `accYieldPerShare` is a running total
/// of yield-per-staked-token scaled by PRECISION; each staker's `yieldDebt` records the value
/// of that accumulator when their stake last changed. Pending yield is therefore
/// `staked * accYieldPerShare / PRECISION - yieldDebt`, which is O(1) per staker and means a
/// staker who joins after a distribution cannot claim any part of it.
contract StakingContract is IStaking {
    using SafeERC20 for IERC20;

    uint256 private constant PRECISION = 1e18;

    IERC20 public immutable token;
    address public owner;
    address public settlementContract;

    uint256 public totalStaked;
    uint256 public accYieldPerShare;

    struct StakeInfo {
        uint256 amount;
        uint256 yieldDebt;
        uint256 accrued;
    }

    mapping(address => StakeInfo) private _stakes;

    error NoStakers();
    error InvalidAmount();

    modifier onlySettlement() {
        require(msg.sender == settlementContract, "StakingContract: caller is not SettlementContract");
        _;
    }

    constructor(address _token) {
        token = IERC20(_token);
        owner = msg.sender;
    }

    /// @notice One-time wiring of the SettlementContract address (deploy-order dependency).
    function setSettlementContract(address _settlement) external {
        require(msg.sender == owner, "StakingContract: caller is not owner");
        require(settlementContract == address(0), "StakingContract: already set");
        settlementContract = _settlement;
    }

    /// @dev Moves any yield accrued under the staker's current position into `accrued`,
    /// so their balance can change without disturbing what they have already earned.
    function _harvest(StakeInfo storage s) private {
        if (s.amount > 0) {
            uint256 entitled = (s.amount * accYieldPerShare) / PRECISION;
            s.accrued += entitled - s.yieldDebt;
        }
    }

    function _syncDebt(StakeInfo storage s) private {
        s.yieldDebt = (s.amount * accYieldPerShare) / PRECISION;
    }

    /// @inheritdoc IStaking
    function stake(uint256 amount) external {
        if (amount == 0) revert InvalidAmount();

        StakeInfo storage s = _stakes[msg.sender];
        _harvest(s);

        token.safeTransferFrom(msg.sender, address(this), amount);
        s.amount += amount;
        totalStaked += amount;

        _syncDebt(s);
        emit Staked(msg.sender, amount);
    }

    /// @inheritdoc IStaking
    function unstake(uint256 amount) external {
        StakeInfo storage s = _stakes[msg.sender];
        if (amount == 0) revert InvalidAmount();
        if (amount > s.amount) revert InsufficientStake();

        _harvest(s);

        s.amount -= amount;
        totalStaked -= amount;

        _syncDebt(s);
        token.safeTransfer(msg.sender, amount);
        emit Unstaked(msg.sender, amount);
    }

    /// @inheritdoc IStaking
    /// @dev Called by SettlementContract after it has transferred `yieldAmount` tokens here.
    /// Reverts NoStakers when the pool is empty — SettlementContract checks `totalStaked`
    /// first and reroutes the staker share to the treasury in that case, so this revert
    /// is a guard rather than a live path.
    function distributeYield(uint256 yieldAmount) external onlySettlement {
        if (yieldAmount == 0) revert InvalidAmount();
        if (totalStaked == 0) revert NoStakers();

        accYieldPerShare += (yieldAmount * PRECISION) / totalStaked;
    }

    /// @inheritdoc IStaking
    function claimYield() external {
        StakeInfo storage s = _stakes[msg.sender];
        _harvest(s);
        _syncDebt(s);

        uint256 payout = s.accrued;
        s.accrued = 0;

        if (payout > 0) {
            token.safeTransfer(msg.sender, payout);
        }
        emit YieldClaimed(msg.sender, payout);
    }

    /// @inheritdoc IStaking
    function getStakeInfo(address staker) external view returns (uint256 staked, uint256 yield_) {
        StakeInfo storage s = _stakes[staker];
        uint256 pending = 0;
        if (s.amount > 0) {
            pending = (s.amount * accYieldPerShare) / PRECISION - s.yieldDebt;
        }
        return (s.amount, s.accrued + pending);
    }
}

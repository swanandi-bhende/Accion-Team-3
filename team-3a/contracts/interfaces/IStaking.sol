// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title IStaking
/// @notice Interface for StakingContract, implementing Requirement 5 (Staking Mechanism).
interface IStaking {
    event Staked(address indexed staker, uint256 amount);
    event Unstaked(address indexed staker, uint256 amount);
    event YieldClaimed(address indexed staker, uint256 amount);

    error InsufficientStake();

    function stake(uint256 amount) external;

    function unstake(uint256 amount) external;

    /// @notice Called by SettlementContract to distribute staker yield proportionally.
    function distributeYield(uint256 yieldAmount) external;

    function claimYield() external;

    function getStakeInfo(address staker) external view returns (uint256 staked, uint256 yield_);

    /// @notice Total principal currently staked across all stakers.
    /// @dev Added post-week-1: SettlementContract needs it to detect an empty pool.
    function totalStaked() external view returns (uint256);
}

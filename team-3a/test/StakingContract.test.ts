import { expect } from "chai";
import { ethers } from "hardhat";
import { loadFixture } from "@nomicfoundation/hardhat-toolbox/network-helpers";
import { deployStack, fundedCompletedTask } from "./fixtures";

describe("StakingContract", function () {
  /// Routes `bounty` through a real settlement so the staking pool receives its 9%
  /// the same way it will in production, rather than via a synthetic transfer.
  async function settleBounty(ctx: any, bounty: bigint, label: string) {
    const taskId = await fundedCompletedTask(ctx, bounty, label);
    await ctx.settlement.settle(taskId, ctx.worker.address);
    return (bounty * 900n) / 10000n;
  }

  // --- Unit tests (5.3) ---

  it("reverts unstaking more than the staked balance with InsufficientStake", async function () {
    const ctx = await loadFixture(deployStack);
    await ctx.staking.connect(ctx.stakerA).stake(ethers.parseEther("100"));

    await expect(
      ctx.staking.connect(ctx.stakerA).unstake(ethers.parseEther("101"))
    ).to.be.revertedWithCustomError(ctx.staking, "InsufficientStake");
  });

  it("distributes yield among multiple stakers in proportion to their stake", async function () {
    const ctx = await loadFixture(deployStack);
    // 3:1 split of the pool.
    await ctx.staking.connect(ctx.stakerA).stake(ethers.parseEther("300"));
    await ctx.staking.connect(ctx.stakerB).stake(ethers.parseEther("100"));

    const yieldAmount = await settleBounty(ctx, ethers.parseEther("1000"), "prop-split");

    const [, yieldA] = await ctx.staking.getStakeInfo(ctx.stakerA.address);
    const [, yieldB] = await ctx.staking.getStakeInfo(ctx.stakerB.address);

    expect(yieldA).to.equal((yieldAmount * 3n) / 4n);
    expect(yieldB).to.equal(yieldAmount / 4n);
  });

  it("claimYield transfers the accumulated amount to the staker", async function () {
    const ctx = await loadFixture(deployStack);
    await ctx.staking.connect(ctx.stakerA).stake(ethers.parseEther("500"));
    const yieldAmount = await settleBounty(ctx, ethers.parseEther("2000"), "claim");

    const before = await ctx.token.balanceOf(ctx.stakerA.address);
    await expect(ctx.staking.connect(ctx.stakerA).claimYield())
      .to.emit(ctx.staking, "YieldClaimed")
      .withArgs(ctx.stakerA.address, yieldAmount);

    expect((await ctx.token.balanceOf(ctx.stakerA.address)) - before).to.equal(yieldAmount);
  });

  it("rejects distributeYield from a non-settlement caller", async function () {
    const ctx = await loadFixture(deployStack);
    await ctx.staking.connect(ctx.stakerA).stake(ethers.parseEther("10"));

    await expect(
      ctx.staking.connect(ctx.outsider).distributeYield(ethers.parseEther("1"))
    ).to.be.revertedWith("StakingContract: caller is not SettlementContract");
  });

  it("a staker who joins after a distribution does not receive that yield", async function () {
    const ctx = await loadFixture(deployStack);
    await ctx.staking.connect(ctx.stakerA).stake(ethers.parseEther("100"));

    const firstYield = await settleBounty(ctx, ethers.parseEther("1000"), "late-join");

    // B joins only after the distribution has already landed.
    await ctx.staking.connect(ctx.stakerB).stake(ethers.parseEther("100"));

    const [, yieldA] = await ctx.staking.getStakeInfo(ctx.stakerA.address);
    const [, yieldB] = await ctx.staking.getStakeInfo(ctx.stakerB.address);

    expect(yieldA).to.equal(firstYield);
    expect(yieldB).to.equal(0n);
  });

  // --- Property tests (5.2) ---

  it("Property 11: stake then unstake nets to zero", async function () {
    const ctx = await loadFixture(deployStack);

    for (const amount of [1n, 1000n, ethers.parseEther("1"), ethers.parseEther("12345.6789")]) {
      const before = await ctx.token.balanceOf(ctx.stakerA.address);

      await ctx.staking.connect(ctx.stakerA).stake(amount);
      await ctx.staking.connect(ctx.stakerA).unstake(amount);

      expect(await ctx.token.balanceOf(ctx.stakerA.address)).to.equal(before);
      const [staked] = await ctx.staking.getStakeInfo(ctx.stakerA.address);
      expect(staked).to.equal(0n);
      expect(await ctx.staking.totalStaked()).to.equal(0n);
    }
  });

  it("Property 12: yield allocation is proportional to pool share across several splits", async function () {
    const splits: Array<[bigint, bigint]> = [
      [ethers.parseEther("1"), ethers.parseEther("1")],
      [ethers.parseEther("900"), ethers.parseEther("100")],
      [ethers.parseEther("7"), ethers.parseEther("13")],
    ];

    for (let i = 0; i < splits.length; i++) {
      const [a, b] = splits[i];
      const ctx = await loadFixture(deployStack);

      await ctx.staking.connect(ctx.stakerA).stake(a);
      await ctx.staking.connect(ctx.stakerB).stake(b);

      const yieldAmount = await settleBounty(ctx, ethers.parseEther("5000"), `prop12-${i}`);
      const pool = a + b;

      const [, yieldA] = await ctx.staking.getStakeInfo(ctx.stakerA.address);
      const [, yieldB] = await ctx.staking.getStakeInfo(ctx.stakerB.address);

      // Accumulator uses 1e18 fixed point, so allow a wei-level dust margin.
      const expectedA = (yieldAmount * a) / pool;
      const expectedB = (yieldAmount * b) / pool;
      const tolerance = 1000n;

      expect(yieldA >= expectedA - tolerance && yieldA <= expectedA + tolerance).to.equal(true);
      expect(yieldB >= expectedB - tolerance && yieldB <= expectedB + tolerance).to.equal(true);
      // No staker may be credited more than the pool received.
      expect(yieldA + yieldB <= yieldAmount).to.equal(true);
    }
  });

  it("Property 13: claiming resets accumulated yield to zero", async function () {
    const ctx = await loadFixture(deployStack);
    await ctx.staking.connect(ctx.stakerA).stake(ethers.parseEther("250"));

    await settleBounty(ctx, ethers.parseEther("1000"), "prop13-a");
    await ctx.staking.connect(ctx.stakerA).claimYield();

    let [, remaining] = await ctx.staking.getStakeInfo(ctx.stakerA.address);
    expect(remaining).to.equal(0n);

    // A second claim with nothing accrued pays out zero and stays at zero.
    await ctx.staking.connect(ctx.stakerA).claimYield();
    [, remaining] = await ctx.staking.getStakeInfo(ctx.stakerA.address);
    expect(remaining).to.equal(0n);

    // Yield accrues again after a further distribution.
    const second = await settleBounty(ctx, ethers.parseEther("400"), "prop13-b");
    [, remaining] = await ctx.staking.getStakeInfo(ctx.stakerA.address);
    expect(remaining).to.equal(second);
  });
});

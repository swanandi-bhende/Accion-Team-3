import { expect } from "chai";
import { ethers } from "hardhat";
import { loadFixture } from "@nomicfoundation/hardhat-toolbox/network-helpers";
import { deployStack, createTask, fundedCompletedTask } from "./fixtures";

describe("SettlementContract", function () {
  /// Stakes a small amount so the pool is non-empty and the 9% share really routes
  /// to StakingContract rather than being rerouted to the treasury.
  async function stackWithStaker() {
    const ctx = await deployStack();
    await ctx.staking.connect(ctx.stakerA).stake(ethers.parseEther("1000"));
    return ctx;
  }

  // --- Unit tests (4.4) ---

  it("splits a known amount exactly 81 / 9 / 10", async function () {
    const ctx = await loadFixture(stackWithStaker);
    const bounty = ethers.parseEther("1000");
    const taskId = await fundedCompletedTask(ctx, bounty);

    const workerBefore = await ctx.token.balanceOf(ctx.worker.address);
    const treasuryBefore = await ctx.token.balanceOf(ctx.treasury.address);
    const stakingBefore = await ctx.token.balanceOf(await ctx.staking.getAddress());

    await ctx.settlement.settle(taskId, ctx.worker.address);

    expect((await ctx.token.balanceOf(ctx.worker.address)) - workerBefore).to.equal(
      ethers.parseEther("810")
    );
    expect((await ctx.token.balanceOf(ctx.treasury.address)) - treasuryBefore).to.equal(
      ethers.parseEther("100")
    );
    expect(
      (await ctx.token.balanceOf(await ctx.staking.getAddress())) - stakingBefore
    ).to.equal(ethers.parseEther("90"));
  });

  it("gives the rounding remainder to the worker and empties the escrow", async function () {
    const ctx = await loadFixture(stackWithStaker);
    // 1001 wei: staker = floor(90.09) = 90, treasury = floor(100.1) = 100, worker = 811.
    const bounty = 1001n;
    const taskId = await fundedCompletedTask(ctx, bounty);

    await expect(ctx.settlement.settle(taskId, ctx.worker.address))
      .to.emit(ctx.settlement, "TaskSettled")
      .withArgs(taskId, ctx.worker.address, 811n, 90n, 100n);

    expect(await ctx.escrow.getBalance(taskId)).to.equal(0n);
  });

  it("reverts settlement of a task with no escrowed funds", async function () {
    const ctx = await loadFixture(stackWithStaker);
    const taskId = await createTask(ctx.taskManager, ctx.creator, ethers.parseEther("10"));
    await ctx.taskManager.connect(ctx.creator).markComplete(taskId);

    await expect(
      ctx.settlement.settle(taskId, ctx.worker.address)
    ).to.be.revertedWithCustomError(ctx.settlement, "InsufficientFunds");
  });

  it("reverts settlement of a task that is not Completed", async function () {
    const ctx = await loadFixture(stackWithStaker);
    const bounty = ethers.parseEther("50");
    const taskId = await createTask(ctx.taskManager, ctx.creator, bounty);
    await ctx.escrow.connect(ctx.creator).deposit(taskId, bounty);

    await expect(
      ctx.settlement.settle(taskId, ctx.worker.address)
    ).to.be.revertedWithCustomError(ctx.settlement, "TaskNotCompleted");
  });

  it("rejects settle from a non-owner", async function () {
    const ctx = await loadFixture(stackWithStaker);
    const taskId = await fundedCompletedTask(ctx, ethers.parseEther("100"));

    await expect(
      ctx.settlement.connect(ctx.outsider).settle(taskId, ctx.worker.address)
    ).to.be.revertedWith("SettlementContract: caller is not owner");
  });

  it("reroutes the staker share to the treasury when the pool is empty", async function () {
    const ctx = await loadFixture(deployStack); // no stakers
    const bounty = ethers.parseEther("1000");
    const taskId = await fundedCompletedTask(ctx, bounty);

    const treasuryBefore = await ctx.token.balanceOf(ctx.treasury.address);

    await expect(ctx.settlement.settle(taskId, ctx.worker.address))
      .to.emit(ctx.settlement, "TaskSettled")
      .withArgs(taskId, ctx.worker.address, ethers.parseEther("810"), 0n, ethers.parseEther("190"));

    expect((await ctx.token.balanceOf(ctx.treasury.address)) - treasuryBefore).to.equal(
      ethers.parseEther("190")
    );
    expect(await ctx.escrow.getBalance(taskId)).to.equal(0n);
  });

  // --- Property tests (4.3) ---

  it("Property 7: worker + staker + treasury always equals the escrowed total", async function () {
    const ctx = await loadFixture(stackWithStaker);

    const amounts = [
      1n, 2n, 7n, 99n, 1000n, 10001n, 123457n,
      ethers.parseEther("0.000001"),
      ethers.parseEther("1"),
      ethers.parseEther("3333.333333333333333333"),
    ];

    for (let i = 0; i < amounts.length; i++) {
      const total = amounts[i];
      const taskId = await fundedCompletedTask(ctx, total, `prop7-${i}`);

      const tx = await ctx.settlement.settle(taskId, ctx.worker.address);
      const receipt = await tx.wait();

      const parsed = receipt!.logs
        .map((l: any) => {
          try { return ctx.settlement.interface.parseLog(l); } catch { return null; }
        })
        .find((l: any) => l && l.name === "TaskSettled");

      const [, , workerAmount, stakerAmount, treasuryAmount] = parsed!.args;
      expect(workerAmount + stakerAmount + treasuryAmount).to.equal(total);
      // Nothing may be stranded in escrow.
      expect(await ctx.escrow.getBalance(taskId)).to.equal(0n);
    }
  });

  it("Property 8: TaskSettled event matches the computed amounts", async function () {
    const ctx = await loadFixture(stackWithStaker);

    for (const total of [ethers.parseEther("500"), 77777n, 10n ** 12n]) {
      const taskId = await fundedCompletedTask(ctx, total, `prop8-${total}`);

      const expectedStaker = (total * 900n) / 10000n;
      const expectedTreasury = (total * 1000n) / 10000n;
      const expectedWorker = total - expectedStaker - expectedTreasury;

      await expect(ctx.settlement.settle(taskId, ctx.worker.address))
        .to.emit(ctx.settlement, "TaskSettled")
        .withArgs(taskId, ctx.worker.address, expectedWorker, expectedStaker, expectedTreasury);
    }
  });

  it("a task cannot be settled twice", async function () {
    const ctx = await loadFixture(stackWithStaker);
    const taskId = await fundedCompletedTask(ctx, ethers.parseEther("100"));

    await ctx.settlement.settle(taskId, ctx.worker.address);
    await expect(
      ctx.settlement.settle(taskId, ctx.worker.address)
    ).to.be.revertedWithCustomError(ctx.settlement, "TaskNotCompleted");
  });
});

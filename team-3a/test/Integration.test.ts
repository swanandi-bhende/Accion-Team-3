import { expect } from "chai";
import { ethers } from "hardhat";
import { loadFixture } from "@nomicfoundation/hardhat-toolbox/network-helpers";
import { deployStack, createTask } from "./fixtures";

describe("Integration: full task lifecycle", function () {
  it("register -> create -> fund -> complete -> settle -> claim", async function () {
    const ctx = await loadFixture(deployStack);
    const bounty = ethers.parseEther("10000");

    // 1. Worker registers an on-chain identity (Req 2).
    const metadata = ethers.keccak256(ethers.toUtf8Bytes("worker-profile-v1"));
    await ctx.identity.connect(ctx.worker).register(metadata);
    expect(await ctx.identity.getIdentity(ctx.worker.address)).to.equal(metadata);

    // 2. Two stakers join the pool 3:1 (Req 5).
    await ctx.staking.connect(ctx.stakerA).stake(ethers.parseEther("3000"));
    await ctx.staking.connect(ctx.stakerB).stake(ethers.parseEther("1000"));
    expect(await ctx.staking.totalStaked()).to.equal(ethers.parseEther("4000"));

    // 3. Creator posts a task (Req 4) and funds escrow (Req 1).
    const taskId = await createTask(ctx.taskManager, ctx.creator, bounty, "e2e");
    await ctx.escrow.connect(ctx.creator).deposit(taskId, bounty);
    expect(await ctx.escrow.getBalance(taskId)).to.equal(bounty);
    expect(await ctx.taskManager.getStatus(taskId)).to.equal(0n); // Open

    // 4. Work is finished.
    await ctx.taskManager.connect(ctx.creator).markComplete(taskId);
    expect(await ctx.taskManager.getStatus(taskId)).to.equal(2n); // Completed

    // 5. Settlement distributes 81 / 9 / 10 (Req 3).
    const workerBefore = await ctx.token.balanceOf(ctx.worker.address);
    const treasuryBefore = await ctx.token.balanceOf(ctx.treasury.address);

    await ctx.settlement.settle(taskId, ctx.worker.address);

    expect((await ctx.token.balanceOf(ctx.worker.address)) - workerBefore).to.equal(
      ethers.parseEther("8100")
    );
    expect((await ctx.token.balanceOf(ctx.treasury.address)) - treasuryBefore).to.equal(
      ethers.parseEther("1000")
    );
    expect(await ctx.escrow.getBalance(taskId)).to.equal(0n);
    expect(await ctx.taskManager.getStatus(taskId)).to.equal(3n); // Settled

    // 6. Stakers claim their proportional share of the 900 token yield.
    const stakerYield = ethers.parseEther("900");
    const aBefore = await ctx.token.balanceOf(ctx.stakerA.address);
    const bBefore = await ctx.token.balanceOf(ctx.stakerB.address);

    await ctx.staking.connect(ctx.stakerA).claimYield();
    await ctx.staking.connect(ctx.stakerB).claimYield();

    expect((await ctx.token.balanceOf(ctx.stakerA.address)) - aBefore).to.equal(
      (stakerYield * 3n) / 4n
    );
    expect((await ctx.token.balanceOf(ctx.stakerB.address)) - bBefore).to.equal(
      stakerYield / 4n
    );

    // 7. Stakers can still withdraw their principal in full.
    await ctx.staking.connect(ctx.stakerA).unstake(ethers.parseEther("3000"));
    const [stakedA] = await ctx.staking.getStakeInfo(ctx.stakerA.address);
    expect(stakedA).to.equal(0n);
  });

  it("emits the three events Team_B subscribes to, with indexed taskId", async function () {
    const ctx = await loadFixture(deployStack);
    await ctx.staking.connect(ctx.stakerA).stake(ethers.parseEther("100"));
    const bounty = ethers.parseEther("500");

    const descHash = ethers.keccak256(ethers.toUtf8Bytes("events"));
    const latest = await ethers.provider.getBlock("latest");
    const deadline = latest!.timestamp + 3600;
    const taskId = await ctx.taskManager
      .connect(ctx.creator)
      .createTask.staticCall(descHash, deadline, bounty);

    await expect(ctx.taskManager.connect(ctx.creator).createTask(descHash, deadline, bounty))
      .to.emit(ctx.taskManager, "TaskCreated")
      .withArgs(taskId, ctx.creator.address, descHash, deadline, bounty);

    await expect(ctx.escrow.connect(ctx.creator).deposit(taskId, bounty))
      .to.emit(ctx.escrow, "TaskFunded")
      .withArgs(taskId, ctx.creator.address, bounty);

    await ctx.taskManager.connect(ctx.creator).markComplete(taskId);

    await expect(ctx.settlement.settle(taskId, ctx.worker.address)).to.emit(
      ctx.settlement,
      "TaskSettled"
    );

    // Filtering by indexed taskId must isolate this task's funding event (Req 8.4).
    const logs = await ctx.escrow.queryFilter(ctx.escrow.filters.TaskFunded(taskId));
    expect(logs.length).to.equal(1);
  });

  it("escrow rejects deposits for a taskId that was never created", async function () {
    const ctx = await loadFixture(deployStack);
    const ghost = ethers.keccak256(ethers.toUtf8Bytes("never-created"));

    await expect(ctx.escrow.connect(ctx.creator).deposit(ghost, ethers.parseEther("5")))
      .to.be.revertedWith("EscrowContract: task does not exist");
  });
});

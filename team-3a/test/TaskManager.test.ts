import { expect } from "chai";
import { ethers } from "hardhat";
import { loadFixture, time } from "@nomicfoundation/hardhat-toolbox/network-helpers";

describe("TaskManager", function () {
  async function deployFixture() {
    const [owner, creator, other, settlementStandin] = await ethers.getSigners();
    const TaskManager = await ethers.getContractFactory("TaskManager");
    const taskManager = await TaskManager.deploy();
    await taskManager.setSettlementContract(settlementStandin.address);
    return { taskManager, owner, creator, other, settlementStandin };
  }

  const descHash = ethers.keccak256(ethers.toUtf8Bytes("task-description"));
  const bounty = ethers.parseEther("100");

  async function futureDeadline(offset = 3600) {
    return (await time.latest()) + offset;
  }

  /// createTask returns the id via return value, which is not readable from a tx receipt;
  /// staticCall gives us the id, then the real tx writes state.
  async function createTask(taskManager: any, signer: any, hash = descHash, amount = bounty) {
    const deadline = await futureDeadline();
    const taskId = await taskManager.connect(signer).createTask.staticCall(hash, deadline, amount);
    await taskManager.connect(signer).createTask(hash, deadline, amount);
    return { taskId, deadline };
  }

  const STATUS_OPEN = 0n;
  const STATUS_COMPLETED = 2n;
  const STATUS_SETTLED = 3n;

  // --- Unit tests (4.x) ---

  it("reverts a past deadline with InvalidDeadline", async function () {
    const { taskManager, creator } = await loadFixture(deployFixture);
    const past = (await time.latest()) - 1;
    await expect(
      taskManager.connect(creator).createTask(descHash, past, bounty)
    ).to.be.revertedWithCustomError(taskManager, "InvalidDeadline");
  });

  it("reverts a zero bounty with InvalidBountyAmount", async function () {
    const { taskManager, creator } = await loadFixture(deployFixture);
    const deadline = await futureDeadline();
    await expect(
      taskManager.connect(creator).createTask(descHash, deadline, 0)
    ).to.be.revertedWithCustomError(taskManager, "InvalidBountyAmount");
  });

  it("stores the task with Open status and the supplied fields", async function () {
    const { taskManager, creator } = await loadFixture(deployFixture);
    const { taskId, deadline } = await createTask(taskManager, creator);

    const task = await taskManager.getTask(taskId);
    expect(task.creator).to.equal(creator.address);
    expect(task.descriptionHash).to.equal(descHash);
    expect(task.deadline).to.equal(deadline);
    expect(task.bountyAmount).to.equal(bounty);
    expect(task.status).to.equal(STATUS_OPEN);
  });

  it("only the creator can mark a task complete", async function () {
    const { taskManager, creator, other } = await loadFixture(deployFixture);
    const { taskId } = await createTask(taskManager, creator);

    await expect(
      taskManager.connect(other).markComplete(taskId)
    ).to.be.revertedWithCustomError(taskManager, "NotTaskCreator");

    await expect(taskManager.connect(creator).markComplete(taskId))
      .to.emit(taskManager, "TaskCompleted")
      .withArgs(taskId);
    expect(await taskManager.getStatus(taskId)).to.equal(STATUS_COMPLETED);
  });

  it("only the SettlementContract can mark a task settled", async function () {
    const { taskManager, creator, settlementStandin } = await loadFixture(deployFixture);
    const { taskId } = await createTask(taskManager, creator);
    await taskManager.connect(creator).markComplete(taskId);

    await expect(taskManager.connect(creator).markSettled(taskId)).to.be.revertedWith(
      "TaskManager: caller is not SettlementContract"
    );

    await taskManager.connect(settlementStandin).markSettled(taskId);
    expect(await taskManager.getStatus(taskId)).to.equal(STATUS_SETTLED);
  });

  it("cannot settle a task that was never completed, or settle twice", async function () {
    const { taskManager, creator, settlementStandin } = await loadFixture(deployFixture);
    const { taskId } = await createTask(taskManager, creator);

    await expect(
      taskManager.connect(settlementStandin).markSettled(taskId)
    ).to.be.revertedWithCustomError(taskManager, "InvalidStatusTransition");

    await taskManager.connect(creator).markComplete(taskId);
    await taskManager.connect(settlementStandin).markSettled(taskId);
    await expect(
      taskManager.connect(settlementStandin).markSettled(taskId)
    ).to.be.revertedWithCustomError(taskManager, "InvalidStatusTransition");
  });

  it("reverts status changes on an unknown taskId with TaskNotFound", async function () {
    const { taskManager, creator } = await loadFixture(deployFixture);
    const ghost = ethers.keccak256(ethers.toUtf8Bytes("no-such-task"));
    await expect(
      taskManager.connect(creator).markComplete(ghost)
    ).to.be.revertedWithCustomError(taskManager, "TaskNotFound");
  });

  // --- Property tests (4.3) ---

  it("Property 9: task creation produces a unique ID with Open status", async function () {
    const { taskManager, creator, other } = await loadFixture(deployFixture);
    const seen = new Set<string>();

    // Same creator, identical arguments, repeated — ids must still differ.
    for (let i = 0; i < 5; i++) {
      const { taskId } = await createTask(taskManager, creator);
      expect(seen.has(taskId)).to.equal(false);
      seen.add(taskId);
      expect(await taskManager.getStatus(taskId)).to.equal(STATUS_OPEN);
    }

    // A different creator with the same arguments also gets a distinct id.
    const { taskId } = await createTask(taskManager, other);
    expect(seen.has(taskId)).to.equal(false);
    expect(await taskManager.taskCount()).to.equal(6n);
  });

  it("Property 10: TaskCreated event contains every input parameter", async function () {
    const { taskManager, creator } = await loadFixture(deployFixture);
    const deadline = await futureDeadline(7200);
    const hash = ethers.keccak256(ethers.toUtf8Bytes("another-description"));
    const amount = ethers.parseEther("42.5");

    const expectedId = await taskManager
      .connect(creator)
      .createTask.staticCall(hash, deadline, amount);

    await expect(taskManager.connect(creator).createTask(hash, deadline, amount))
      .to.emit(taskManager, "TaskCreated")
      .withArgs(expectedId, creator.address, hash, deadline, amount);
  });
});

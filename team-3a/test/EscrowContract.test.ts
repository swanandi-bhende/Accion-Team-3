import { expect } from "chai";
import { ethers } from "hardhat";
import { loadFixture } from "@nomicfoundation/hardhat-toolbox/network-helpers";

describe("EscrowContract", function () {
  async function deployFixture() {
    const [owner, depositor, otherDepositor, settlementStandin] = await ethers.getSigners();

    const Token = await ethers.getContractFactory("TestERC20");
    const token = await Token.deploy("Mock USD", "mUSD", ethers.parseEther("1000000"));

    const Escrow = await ethers.getContractFactory("EscrowContract");
    const escrow = await Escrow.deploy(await token.getAddress());
    await escrow.setSettlementContract(settlementStandin.address);

    const TaskManager = await ethers.getContractFactory("TaskManager");
    const taskManager = await TaskManager.deploy();
    await escrow.setTaskManagerContract(await taskManager.getAddress());

    const descHash = ethers.keccak256(ethers.toUtf8Bytes("task-1"));
    const deadline = (await ethers.provider.getBlock("latest"))!.timestamp + 3600;
    const bounty = ethers.parseEther("1000");
    const taskId = await taskManager.connect(depositor).createTask.staticCall(descHash, deadline, bounty);
    await taskManager.connect(depositor).createTask(descHash, deadline, bounty);

    await token.transfer(depositor.address, ethers.parseEther("1000"));
    await token.transfer(otherDepositor.address, ethers.parseEther("1000"));
    await token.connect(depositor).approve(await escrow.getAddress(), ethers.MaxUint256);
    await token.connect(otherDepositor).approve(await escrow.getAddress(), ethers.MaxUint256);

    return { escrow, token, owner, depositor, otherDepositor, settlementStandin, taskId };
  }

  // --- Unit tests (2.3) ---

  it("reverts on zero-amount deposit with InvalidAmount", async function () {
    const { escrow, depositor, taskId } = await loadFixture(deployFixture);
    await expect(escrow.connect(depositor).deposit(taskId, 0)).to.be.revertedWithCustomError(
      escrow,
      "InvalidAmount"
    );
  });

  it("accumulates multiple deposits for the same taskId", async function () {
    const { escrow, depositor, taskId } = await loadFixture(deployFixture);
    await escrow.connect(depositor).deposit(taskId, ethers.parseEther("10"));
    await escrow.connect(depositor).deposit(taskId, ethers.parseEther("15"));
    expect(await escrow.getBalance(taskId)).to.equal(ethers.parseEther("25"));
  });

  it("release transfers tokens to the correct recipient", async function () {
    const { escrow, token, depositor, otherDepositor, settlementStandin, taskId } = await loadFixture(deployFixture);
    await escrow.connect(depositor).deposit(taskId, ethers.parseEther("10"));

    const before = await token.balanceOf(otherDepositor.address);
    await escrow.connect(settlementStandin).release(taskId, otherDepositor.address, ethers.parseEther("4"));
    const after = await token.balanceOf(otherDepositor.address);

    expect(after - before).to.equal(ethers.parseEther("4"));
    expect(await escrow.getBalance(taskId)).to.equal(ethers.parseEther("6"));
  });

  it("reverts an unauthorized release call", async function () {
    const { escrow, depositor, otherDepositor, taskId } = await loadFixture(deployFixture);
    await escrow.connect(depositor).deposit(taskId, ethers.parseEther("10"));
    await expect(
      escrow.connect(depositor).release(taskId, otherDepositor.address, ethers.parseEther("1"))
    ).to.be.revertedWith("EscrowContract: caller is not SettlementContract");
  });

  it("rejects direct withdrawal attempts (Requirement 1.3)", async function () {
    const { escrow, depositor, taskId } = await loadFixture(deployFixture);
    await escrow.connect(depositor).deposit(taskId, ethers.parseEther("10"));
    await expect(escrow.connect(depositor).withdraw(taskId)).to.be.revertedWithCustomError(
      escrow,
      "WithdrawalNotAllowed"
    );
  });

  // --- Property tests (2.2) ---

  it("Property 1: escrow balance equals sum of all deposits for a task", async function () {
    const { escrow, depositor, otherDepositor, taskId } = await loadFixture(deployFixture);
    const amounts = [
      ethers.parseEther("3"),
      ethers.parseEther("7"),
      ethers.parseEther("1.5"),
    ];

    await escrow.connect(depositor).deposit(taskId, amounts[0]);
    await escrow.connect(otherDepositor).deposit(taskId, amounts[1]);
    await escrow.connect(depositor).deposit(taskId, amounts[2]);

    const expectedTotal = amounts.reduce((a, b) => a + b, 0n);
    expect(await escrow.getBalance(taskId)).to.equal(expectedTotal);
  });

  it("Property 2: withdrawal is always rejected while funds are held", async function () {
    const { escrow, depositor, taskId } = await loadFixture(deployFixture);
    await escrow.connect(depositor).deposit(taskId, ethers.parseEther("5"));
    await expect(escrow.connect(depositor).withdraw(taskId)).to.be.reverted;
  });

  it("Property 3: TaskFunded event params exactly match the deposit transaction", async function () {
    const { escrow, depositor, taskId } = await loadFixture(deployFixture);
    const amount = ethers.parseEther("9.5");

    await expect(escrow.connect(depositor).deposit(taskId, amount))
      .to.emit(escrow, "TaskFunded")
      .withArgs(taskId, depositor.address, amount);
  });
});

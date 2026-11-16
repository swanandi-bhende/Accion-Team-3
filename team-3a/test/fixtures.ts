import { ethers } from "hardhat";

/// Deploys the full stack in dependency order and performs the post-deploy wiring.
/// Mirrors deploy/deploy.ts so tests exercise the same topology as a real deployment.
export async function deployStack() {
  const [owner, creator, worker, stakerA, stakerB, treasury, outsider] =
    await ethers.getSigners();

  const Token = await ethers.getContractFactory("TestERC20");
  const token = await Token.deploy("Mock USD", "mUSD", ethers.parseEther("10000000"));

  const Escrow = await ethers.getContractFactory("EscrowContract");
  const escrow = await Escrow.deploy(await token.getAddress());

  const Staking = await ethers.getContractFactory("StakingContract");
  const staking = await Staking.deploy(await token.getAddress());

  const Identity = await ethers.getContractFactory("IdentityContract");
  const identity = await Identity.deploy();

  const TaskManager = await ethers.getContractFactory("TaskManager");
  const taskManager = await TaskManager.deploy();

  const Settlement = await ethers.getContractFactory("SettlementContract");
  const settlement = await Settlement.deploy(
    await escrow.getAddress(),
    await staking.getAddress(),
    await taskManager.getAddress(),
    treasury.address
  );

  const settlementAddr = await settlement.getAddress();
  await escrow.setSettlementContract(settlementAddr);
  await escrow.setTaskManagerContract(await taskManager.getAddress());
  await staking.setSettlementContract(settlementAddr);
  await taskManager.setSettlementContract(settlementAddr);

  // Fund participants and pre-approve the contracts that pull tokens.
  for (const signer of [creator, stakerA, stakerB]) {
    await token.transfer(signer.address, ethers.parseEther("100000"));
    await token.connect(signer).approve(await escrow.getAddress(), ethers.MaxUint256);
    await token.connect(signer).approve(await staking.getAddress(), ethers.MaxUint256);
  }

  return {
    token, escrow, staking, identity, taskManager, settlement,
    owner, creator, worker, stakerA, stakerB, treasury, outsider,
  };
}

/// Creates a task and returns its id. createTask returns the id as a return value,
/// which a receipt cannot expose, so staticCall reads it before the real tx writes state.
export async function createTask(
  taskManager: any,
  creator: any,
  bounty: bigint,
  label = "task"
) {
  const descHash = ethers.keccak256(ethers.toUtf8Bytes(label));
  const latest = await ethers.provider.getBlock("latest");
  const deadline = latest!.timestamp + 3600;
  const taskId = await taskManager
    .connect(creator)
    .createTask.staticCall(descHash, deadline, bounty);
  await taskManager.connect(creator).createTask(descHash, deadline, bounty);
  return taskId as string;
}

/// Full path from creation to a settleable task: create, fund escrow, mark complete.
export async function fundedCompletedTask(
  ctx: Awaited<ReturnType<typeof deployStack>>,
  bounty: bigint,
  label = "task"
) {
  const taskId = await createTask(ctx.taskManager, ctx.creator, bounty, label);
  await ctx.escrow.connect(ctx.creator).deposit(taskId, bounty);
  await ctx.taskManager.connect(ctx.creator).markComplete(taskId);
  return taskId;
}

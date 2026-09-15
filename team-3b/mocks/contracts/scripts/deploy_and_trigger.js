const hre = require("hardhat");
const fs = require("fs");

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  console.log("Deploying mock contracts with account:", deployer.address);

  const TaskManager = await hre.ethers.getContractFactory("TaskManager");
  const taskManager = await TaskManager.deploy();
  await taskManager.waitForDeployment();
  const taskManagerAddress = await taskManager.getAddress();

  const Escrow = await hre.ethers.getContractFactory("Escrow");
  const escrow = await Escrow.deploy();
  await escrow.waitForDeployment();
  const escrowAddress = await escrow.getAddress();

  console.log("TaskManager deployed to:", taskManagerAddress);
  console.log("Escrow deployed to:", escrowAddress);

  // Write to .env.mock for local dev
  const envMockContent = `TASK_MANAGER_ADDRESS=${taskManagerAddress}\nESCROW_ADDRESS=${escrowAddress}\n`;
  fs.writeFileSync("../../.env.mock", envMockContent);
  console.log("Wrote addresses to .env.mock");

  // Optional: Trigger a mock task immediately if requested
  if (process.env.TRIGGER_MOCK_TASK === "true") {
    console.log("Triggering mock task...");
    const descHash = hre.ethers.id("mock_model_123");
    const deadline = Math.floor(Date.now() / 1000) + 3600; // 1 hour from now
    const bounty = hre.ethers.parseEther("1.0");

    const tx1 = await taskManager.createTask(descHash, deadline, bounty);
    const receipt1 = await tx1.wait();
    
    // Parse the taskId from the event
    const event = receipt1.logs[0];
    const taskId = event.args ? event.args[0] : event.topics[1]; 
    console.log(`TaskCreated triggered with taskId: ${taskId}`);

    const tx2 = await escrow.fundTask(taskId, { value: bounty });
    await tx2.wait();
    console.log(`TaskFunded triggered for taskId: ${taskId}`);
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

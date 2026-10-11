const hre = require("hardhat");
const fs = require("fs");

const NODE_WAIT_TIMEOUT_MS = 60000;

// Under compose the node is a separate container that may still be installing
// dependencies, so wait for the RPC to answer instead of racing it with a sleep.
async function waitForChain() {
  const deadline = Date.now() + NODE_WAIT_TIMEOUT_MS;
  for (;;) {
    try {
      await hre.network.provider.send("eth_chainId");
      return;
    } catch (error) {
      if (Date.now() >= deadline) {
        throw error;
      }
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  }
}

async function main() {
  await waitForChain();
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

  // Write to .env.mock for local dev. The mock TaskManager also exposes
  // submitProofs, so it *is* the local settlement contract.
  const addresses = {
    TASK_MANAGER_ADDRESS: taskManagerAddress,
    ESCROW_ADDRESS: escrowAddress,
    PROOF_SUBMITTER_CONTRACT_ADDRESS: taskManagerAddress,
  };
  const envMockContent = Object.entries(addresses)
    .map(([key, value]) => `${key}=${value}`)
    .join("\n") + "\n";
  fs.writeFileSync("../../.env.mock", envMockContent);
  console.log("Wrote addresses to .env.mock");

  // Machine-readable copy for services that start before .env.mock exists: it is
  // read through MOCK_DEPLOYMENTS_PATH (see common/config.py).
  fs.mkdirSync("deployments", { recursive: true });
  fs.writeFileSync(
    "deployments/localhost.json",
    JSON.stringify(addresses, null, 2) + "\n"
  );
  console.log("Wrote addresses to deployments/localhost.json");

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

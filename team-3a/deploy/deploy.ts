import { ethers, network, config as hhConfig } from "hardhat";
import { execSync } from "child_process";
import * as fs from "fs";
import * as path from "path";

/// Deploys the full stack in dependency order (Task 13.3).
/// Escrow, Staking and TaskManager each need the SettlementContract address, but
/// SettlementContract needs theirs first — the cycle is broken by deploying them
/// unwired and calling their one-time setters afterwards.
///
/// Galileo (chain ID 16602) is the only supported public testnet. The script
/// enforces the chain ID from the network config, so a misconfigured RPC that
/// actually connects to a different chain is caught before any transaction is sent.
async function main() {
  const [deployer] = await ethers.getSigners();
  const treasury = process.env.TREASURY_ADDRESS || deployer.address;

  console.log("Deployer:", deployer.address);
  console.log("Treasury:", treasury);
  console.log("Network: ", network.name);

  // -------------------------------------------------------------------------
  // Chain-ID validation: refuse to deploy if the live chain does not match
  // the chain ID declared in hardhat.config.ts. Catches a misconfigured RPC
  // URL (e.g. OG_RPC_URL pointing at the wrong endpoint) before any gas is
  // spent.
  // -------------------------------------------------------------------------
  const liveChainId = Number((await ethers.provider.getNetwork()).chainId);
  const configuredChainId = (network.config as { chainId?: number }).chainId;

  if (configuredChainId !== undefined && liveChainId !== configuredChainId) {
    throw new Error(
      `Chain ID mismatch: RPC returned ${liveChainId} but hardhat.config.ts ` +
        `declares ${configuredChainId} for network "${network.name}". ` +
        `Check your OG_RPC_URL / SEPOLIA_RPC_URL environment variable.`
    );
  }

  const chainId = liveChainId;
  const isLocal = chainId === 31337;

  // Galileo is the only supported public testnet. Fail loudly on unknown chain IDs
  // so we never accidentally send transactions to an unintended network.
  const GALILEO_CHAIN_ID = 16602;
  const SEPOLIA_CHAIN_ID = 11155111;
  const allowedChainIds = [31337, GALILEO_CHAIN_ID, SEPOLIA_CHAIN_ID];
  if (!allowedChainIds.includes(chainId)) {
    throw new Error(
      `Unsupported chain ID ${chainId}. This script only targets ` +
        `localhost (31337), Galileo (16602), or Sepolia (11155111). ` +
        `No mainnet deployment path exists.`
    );
  }

  console.log(`Chain ID: ${chainId} — ${isLocal ? "local" : network.name}`);

  // TestERC20 is a mock and must never back a real deployment. On any chain other
  // than the local Hardhat node, ESCROW_TOKEN_ADDRESS is required and used as-is.
  const configuredToken = process.env.ESCROW_TOKEN_ADDRESS;

  let tokenAddress: string;
  if (configuredToken) {
    tokenAddress = ethers.getAddress(configuredToken);
    console.log("Using configured ERC20 token:", tokenAddress);
  } else if (isLocal) {
    const Token = await ethers.getContractFactory("TestERC20");
    const mock = await Token.deploy("Mock Token", "MOCK", ethers.parseEther("10000000"));
    await mock.waitForDeployment();
    tokenAddress = await mock.getAddress();
    console.log("Local chain: deployed mock TestERC20 at", tokenAddress);
  } else {
    throw new Error(
      `ESCROW_TOKEN_ADDRESS is required for chainId ${chainId} (${network.name}). ` +
        "Refusing to back a live deployment with the TestERC20 mock. " +
        "Provide the address of a real ERC-20 token on this network."
    );
  }

  // -------------------------------------------------------------------------
  // Deploy contracts in dependency order. Settlement depends on the other four;
  // the other four get wired to Settlement via one-time setters after Settlement
  // is deployed (circular-reference resolution).
  // -------------------------------------------------------------------------

  console.log("\n--- Deploying EscrowContract ---");
  const Escrow = await ethers.getContractFactory("EscrowContract");
  const escrow = await Escrow.deploy(tokenAddress);
  await escrow.waitForDeployment();
  const escrowReceipt = escrow.deploymentTransaction();
  console.log("EscrowContract:", await escrow.getAddress());

  console.log("--- Deploying StakingContract ---");
  const Staking = await ethers.getContractFactory("StakingContract");
  const staking = await Staking.deploy(tokenAddress);
  await staking.waitForDeployment();
  const stakingReceipt = staking.deploymentTransaction();
  console.log("StakingContract:", await staking.getAddress());

  console.log("--- Deploying IdentityContract ---");
  const Identity = await ethers.getContractFactory("IdentityContract");
  const identity = await Identity.deploy();
  await identity.waitForDeployment();
  const identityReceipt = identity.deploymentTransaction();
  console.log("IdentityContract:", await identity.getAddress());

  console.log("--- Deploying TaskManager ---");
  const TaskManager = await ethers.getContractFactory("TaskManager");
  const taskManager = await TaskManager.deploy();
  await taskManager.waitForDeployment();
  const taskManagerReceipt = taskManager.deploymentTransaction();
  console.log("TaskManager:", await taskManager.getAddress());

  console.log("--- Deploying SettlementContract ---");
  const Settlement = await ethers.getContractFactory("SettlementContract");
  const settlement = await Settlement.deploy(
    await escrow.getAddress(),
    await staking.getAddress(),
    await taskManager.getAddress(),
    treasury
  );
  await settlement.waitForDeployment();
  const settlementReceipt = settlement.deploymentTransaction();
  console.log("SettlementContract:", await settlement.getAddress());

  // -------------------------------------------------------------------------
  // Wire the circular dependencies via one-time setters.
  // -------------------------------------------------------------------------
  console.log("\n--- Wiring circular dependencies ---");
  const settlementAddr = await settlement.getAddress();
  await (await escrow.setSettlementContract(settlementAddr)).wait();
  console.log("EscrowContract.setSettlementContract ✓");
  await (await escrow.setTaskManagerContract(await taskManager.getAddress())).wait();
  console.log("EscrowContract.setTaskManagerContract ✓");
  await (await staking.setSettlementContract(settlementAddr)).wait();
  console.log("StakingContract.setSettlementContract ✓");
  await (await taskManager.setSettlementContract(settlementAddr)).wait();
  console.log("TaskManager.setSettlementContract ✓");

  // Team 3b's funded signer gets the prover role so it can call submitProofs.
  const prover = process.env.PROVER_ADDRESS ? ethers.getAddress(process.env.PROVER_ADDRESS) : null;
  if (prover) {
    await (await taskManager.setProver(prover, true)).wait();
    console.log("Granted prover role to", prover);
  } else {
    console.log("PROVER_ADDRESS not set — grant later with TaskManager.setProver(address, true)");
  }

  // Token facts consumers need (Team 3c's frontend must not assume 18 decimals).
  let tokenMeta: { symbol: string | null; decimals: number | null } = { symbol: null, decimals: null };
  try {
    const erc20 = new ethers.Contract(
      tokenAddress,
      ["function symbol() view returns (string)", "function decimals() view returns (uint8)"],
      ethers.provider
    );
    tokenMeta = { symbol: await erc20.symbol(), decimals: Number(await erc20.decimals()) };
  } catch {
    console.warn("Could not read token symbol/decimals — manifest will record null.");
  }

  let gitCommit: string | null = process.env.GIT_COMMIT || null;
  if (!gitCommit) {
    try {
      gitCommit = execSync("git rev-parse HEAD", { stdio: ["ignore", "pipe", "ignore"] }).toString().trim();
    } catch {
      gitCommit = null;
    }
  }

  const solc = hhConfig.solidity.compilers[0];
  const blockNumber = await ethers.provider.getBlockNumber();

  // -------------------------------------------------------------------------
  // Build and persist the deployment manifest.
  // Each network writes to its own file (galileo.json, sepolia.json, etc.)
  // so manifests are never overwritten across networks.
  // Transaction hashes are recorded directly from the deployment receipts;
  // they are never invented.
  // -------------------------------------------------------------------------
  const escrowAddr = await escrow.getAddress();
  const stakingAddr = await staking.getAddress();
  const identityAddr = await identity.getAddress();
  const taskManagerAddr = await taskManager.getAddress();

  const manifest = {
    network: network.name,
    chainId,
    // Start scanning events here instead of from genesis (saves indexer time).
    deployedAtBlock: blockNumber,
    deployedAt: new Date().toISOString(),
    deployer: deployer.address,
    gitCommit,
    compiler: {
      version: solc.version,
      evmVersion: (solc.settings as { evmVersion?: string }).evmVersion ?? "default",
      optimizer: solc.settings.optimizer,
    },
    constructorArgs: {
      EscrowContract: { token: tokenAddress },
      StakingContract: { token: tokenAddress },
      IdentityContract: {},
      TaskManager: {},
      SettlementContract: {
        escrow: escrowAddr,
        staking: stakingAddr,
        taskManager: taskManagerAddr,
        treasury,
      },
    },
    initCalls: {
      "EscrowContract.setSettlementContract": settlementAddr,
      "EscrowContract.setTaskManagerContract": taskManagerAddr,
      "StakingContract.setSettlementContract": settlementAddr,
      "TaskManager.setSettlementContract": settlementAddr,
      ...(prover ? { "TaskManager.setProver": `${prover} → true` } : {}),
    },
    Token: tokenAddress,
    TokenSymbol: tokenMeta.symbol,
    TokenDecimals: tokenMeta.decimals,
    Prover: prover,
    EscrowContract: escrowAddr,
    StakingContract: stakingAddr,
    IdentityContract: identityAddr,
    TaskManager: taskManagerAddr,
    SettlementContract: settlementAddr,
    Treasury: treasury,
    // Transaction hashes — populated from on-chain receipts (never invented).
    // On the local Hardhat node some hashes may be null (instant mining).
    txHashes: {
      EscrowContract: escrowReceipt?.hash ?? null,
      StakingContract: stakingReceipt?.hash ?? null,
      IdentityContract: identityReceipt?.hash ?? null,
      TaskManager: taskManagerReceipt?.hash ?? null,
      SettlementContract: settlementReceipt?.hash ?? null,
    },
  };

  const outDir = path.join(__dirname, "..", "deployments");
  fs.mkdirSync(outDir, { recursive: true });
  const outFile = path.join(outDir, `${manifest.network}.json`);
  fs.writeFileSync(outFile, JSON.stringify(manifest, null, 2));

  console.log("\nDeployed:", manifest);
  console.log("Addresses written to", outFile);
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});

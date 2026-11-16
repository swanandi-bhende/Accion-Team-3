import { ethers, network } from "hardhat";
import * as fs from "fs";
import * as path from "path";

/// End-to-end smoke test for the cross-team staging run. Walks one task through every
/// handoff: create -> bid (3c) -> fund -> accept -> submitProofs (3b) -> settleTask.
///
/// Reads addresses from deployments/<network>.json, so it runs unchanged against
/// localhost or Sepolia. Roles are taken from the configured signers: signer[0] is the
/// deployer/owner and must hold the prover role (set PROVER_ADDRESS at deploy time, or
/// let this script grant it). On a live network, set SMOKE_WORKER_KEY to a separate funded
/// key; locally it uses the next Hardhat account.
async function main() {
  const file = path.join(__dirname, "..", "deployments", `${network.name}.json`);
  if (!fs.existsSync(file)) throw new Error(`No manifest at ${file}. Deploy first.`);
  const d = JSON.parse(fs.readFileSync(file, "utf8"));

  const signers = await ethers.getSigners();
  const owner = signers[0];
  let worker: any;
  if (process.env.SMOKE_WORKER_KEY) {
    worker = new ethers.Wallet(process.env.SMOKE_WORKER_KEY, ethers.provider);
  } else if (signers.length > 1) {
    worker = signers[1];
  } else {
    console.log("Creating and funding ephemeral worker wallet for live smoke test...");
    const ephem = ethers.Wallet.createRandom(ethers.provider);
    const fundTx = await owner.sendTransaction({
      to: ephem.address,
      value: ethers.parseEther("0.02"),
    });
    await fundTx.wait();
    console.log("Funded ephemeral worker:", ephem.address);
    worker = ephem;
  }

  const tm = await ethers.getContractAt("TaskManager", d.TaskManager, owner);
  const escrow = await ethers.getContractAt("EscrowContract", d.EscrowContract, owner);
  const settlement = await ethers.getContractAt("SettlementContract", d.SettlementContract, owner);
  const token = await ethers.getContractAt("TestERC20", d.Token, owner);

  if (!(await tm.isProver(owner.address))) {
    console.log("Granting prover role to", owner.address);
    await (await tm.setProver(owner.address, true)).wait();
  }

  const decimals = d.TokenDecimals ?? 18;
  const bounty = ethers.parseUnits("10", decimals);
  const latest = await ethers.provider.getBlock("latest");
  const descHash = ethers.keccak256(ethers.toUtf8Bytes(`smoke-${Date.now()}`));
  const taskId = await tm.createTask.staticCall(descHash, latest!.timestamp + 3600, bounty);
  await (await tm.createTask(descHash, latest!.timestamp + 3600, bounty)).wait();
  console.log("created   ", taskId);

  await (await tm.connect(worker).placeBid(taskId, bounty, 300, ethers.ZeroHash)).wait();
  await (await token.approve(d.EscrowContract, bounty)).wait();
  await (await escrow.deposit(taskId, bounty)).wait();
  await (await tm.acceptBid(taskId, worker.address)).wait();
  console.log("funded + worker accepted:", worker.address);

  const proofHash = ethers.keccak256(ethers.toUtf8Bytes("proof"));
  const resultHash = ethers.keccak256(ethers.toUtf8Bytes("result"));
  await (await tm.submitProofs([taskId], [proofHash], ["bafy-smoke-model-cid"], [resultHash])).wait();
  console.log("proof anchored, status =", await tm.getStatus(taskId));

  const before = await token.balanceOf(worker.address);
  await (await settlement.settleTask(taskId)).wait();
  const paid = (await token.balanceOf(worker.address)) - before;
  console.log("settled. worker received", ethers.formatUnits(paid, decimals), "(expected 81% of 10)");
  if (paid !== (bounty * 81n) / 100n) throw new Error("worker payout is not 81% of the bounty");
  console.log("SMOKE TEST PASSED");
}

main().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});

import { ethers, network } from "hardhat";
import * as fs from "fs";
import * as path from "path";

async function main() {
  const file = path.join(__dirname, "..", "deployments", `${network.name}.json`);
  if (!fs.existsSync(file)) throw new Error(`No manifest at ${file}`);
  const d = JSON.parse(fs.readFileSync(file, "utf8"));

  const proverAddr = process.env.PROVER_ADDRESS;
  if (!proverAddr || !ethers.isAddress(proverAddr)) {
    throw new Error(`Invalid PROVER_ADDRESS in .env: ${proverAddr}`);
  }

  const [owner] = await ethers.getSigners();
  console.log("Network:", network.name);
  console.log("Caller (Owner):", owner.address);
  console.log("Granting prover role to:", proverAddr);

  const tm = await ethers.getContractAt("TaskManager", d.TaskManager, owner);

  const alreadyProver = await tm.isProver(proverAddr);
  if (alreadyProver) {
    console.log("Address is already granted the prover role.");
  } else {
    const tx = await tm.setProver(proverAddr, true);
    console.log("Transaction submitted, hash:", tx.hash);
    const receipt = await tx.wait();
    console.log("Transaction confirmed in block:", receipt?.blockNumber);
  }

  const verified = await tm.isProver(proverAddr);
  console.log("Verification - isProver:", verified);
  if (!verified) throw new Error("Failed to verify prover role on contract");

  d.Prover = proverAddr;
  fs.writeFileSync(file, JSON.stringify(d, null, 2));
  console.log("Updated deployments manifest with Prover address.");
}

main().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});

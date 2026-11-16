import { run, network } from "hardhat";
import * as fs from "fs";
import * as path from "path";

/// Publishes every deployed contract's source to the block explorer and prints
/// the verified-source links for the handoff doc. Supports Sepolia (Etherscan)
/// and 0G Galileo (Chainscan). Constructor arguments mirror deploy/deploy.ts.
async function main() {
  const file = path.join(__dirname, "..", "deployments", `${network.name}.json`);
  if (!fs.existsSync(file)) throw new Error(`No manifest at ${file}. Deploy first.`);
  const d = JSON.parse(fs.readFileSync(file, "utf8"));

  const targets: [string, string, string[]][] = [
    ["EscrowContract", d.EscrowContract, [d.Token]],
    ["StakingContract", d.StakingContract, [d.Token]],
    ["IdentityContract", d.IdentityContract, []],
    ["TaskManager", d.TaskManager, []],
    ["SettlementContract", d.SettlementContract, [d.EscrowContract, d.StakingContract, d.TaskManager, d.Treasury]],
  ];

  // Explorer base URLs per network (address page with #code anchor).
  const explorerBases: Record<string, string> = {
    sepolia: "https://sepolia.etherscan.io/address/",
    galileo: "https://chainscan-galileo.0g.ai/address/",
  };
  const explorerBase = explorerBases[network.name] ?? null;

  const links: Record<string, string> = {};
  for (const [name, address, args] of targets) {
    try {
      await run("verify:verify", { address, constructorArguments: args });
    } catch (e: any) {
      if (!/already verified/i.test(String(e?.message))) throw e;
    }
    if (explorerBase) links[name] = `${explorerBase}${address}#code`;
    console.log(`${name}: verified`);
  }
  if (explorerBase) {
    d.explorer = links;
    fs.writeFileSync(file, JSON.stringify(d, null, 2));
    console.log("Explorer links written to", file);
  }
}

main().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});


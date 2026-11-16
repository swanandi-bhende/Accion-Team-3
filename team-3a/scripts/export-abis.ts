import * as fs from "fs";
import * as path from "path";

/// Publishes the ABIs and deployed addresses Team B needs to subscribe to
/// TaskCreated / TaskFunded / TaskSettled.
///
/// artifacts/ is generated and gitignored, so without this step the ABIs only exist
/// on the machine that last ran `hardhat compile`. This writes a committed, versioned
/// bundle to exports/ instead.

const CONTRACTS = [
  "EscrowContract",
  "IdentityContract",
  "SettlementContract",
  "StakingContract",
  "TaskManager",
];

const ROOT = path.join(__dirname, "..");
const ARTIFACTS = path.join(ROOT, "artifacts", "contracts");
const OUT_DIR = path.join(ROOT, "exports");

function findArtifact(name: string): string {
  const stack = [ARTIFACTS];
  while (stack.length) {
    const dir = stack.pop()!;
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) stack.push(full);
      else if (entry.name === `${name}.json`) return full;
    }
  }
  throw new Error(`No artifact for ${name}. Run \`npm run compile\` first.`);
}

function main() {
  if (!fs.existsSync(ARTIFACTS)) {
    throw new Error("artifacts/ not found. Run `npm run compile` first.");
  }
  fs.mkdirSync(OUT_DIR, { recursive: true });

  const bundle: Record<string, unknown> = {};
  const events: Record<string, string[]> = {};

  for (const name of CONTRACTS) {
    const artifact = JSON.parse(fs.readFileSync(findArtifact(name), "utf8"));
    bundle[name] = artifact.abi;
    events[name] = artifact.abi
      .filter((item: any) => item.type === "event")
      .map((item: any) => {
        const args = item.inputs
          .map((i: any) => `${i.type}${i.indexed ? " indexed" : ""} ${i.name}`)
          .join(", ");
        return `${item.name}(${args})`;
      });
    fs.writeFileSync(
      path.join(OUT_DIR, `${name}.abi.json`),
      JSON.stringify(artifact.abi, null, 2)
    );
  }

  fs.writeFileSync(path.join(OUT_DIR, "abis.json"), JSON.stringify(bundle, null, 2));

  // Copy whatever deployment manifests exist so addresses ship alongside the ABIs.
  const deploymentsDir = path.join(ROOT, "deployments");
  const deployments: Record<string, unknown> = {};
  if (fs.existsSync(deploymentsDir)) {
    for (const file of fs.readdirSync(deploymentsDir)) {
      // hardhat/localhost manifests are gitignored scratch output. Including them would
      // make exports/ differ per machine and break the CI freshness check.
      const name = file.replace(/\.json$/, "");
      if (file.endsWith(".json") && name !== "hardhat" && name !== "localhost") {
        deployments[file.replace(/\.json$/, "")] = JSON.parse(
          fs.readFileSync(path.join(deploymentsDir, file), "utf8")
        );
      }
    }
  }

  fs.writeFileSync(
    path.join(OUT_DIR, "index.json"),
    // No timestamp: the output must be byte-identical for identical contracts, otherwise
    // CI's `git diff --exit-code exports/` could never pass.
    JSON.stringify({ events, deployments }, null, 2)
  );

  console.log(`Exported ${CONTRACTS.length} ABIs to exports/`);
  for (const [name, list] of Object.entries(events)) {
    if (list.length) console.log(`  ${name}: ${list.length} event(s)`);
  }
}

main();

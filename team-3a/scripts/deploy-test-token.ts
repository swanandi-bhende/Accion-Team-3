/**
 * deploy-test-token.ts — Deploys TestERC20 on a non-local testnet.
 *
 * ⚠️  TESTNET / DEVELOPMENT ONLY. Never run this against a mainnet.
 *
 * This is an explicit opt-in step for teams that do not yet have a real ERC-20
 * on Galileo. It deploys the same TestERC20 contract used in local unit tests
 * as a "faucet token" so you can set ESCROW_TOKEN_ADDRESS and proceed with
 * the main deploy:galileo command.
 *
 * Usage:
 *   npx hardhat run scripts/deploy-test-token.ts --network galileo
 *
 * After it runs:
 *   1. Copy the printed address.
 *   2. Add ESCROW_TOKEN_ADDRESS=0x<addr> to your .env.
 *   3. Run npm run deploy:galileo.
 *
 * The minted supply goes entirely to the deployer. You can transfer tokens to
 * other test wallets (escrow depositors, stakers) using any wallet or cast/ethers.
 *
 * DO NOT use this token in any context where real value is involved. It is a
 * test artifact, not a production asset.
 */

import { ethers, network } from "hardhat";
import * as fs from "fs";
import * as path from "path";

const ALLOWED_TESTNET_CHAIN_IDS = [16602, 11155111]; // Galileo, Sepolia

async function main() {
  const chainId = Number((await ethers.provider.getNetwork()).chainId);

  if (chainId === 31337) {
    console.log(
      "On the local Hardhat node, deploy:local already deploys TestERC20 automatically.\n" +
        "Run this script on a public testnet (galileo / sepolia) only."
    );
    process.exit(0);
  }

  if (!ALLOWED_TESTNET_CHAIN_IDS.includes(chainId)) {
    throw new Error(
      `Chain ID ${chainId} is not a known testnet. Refusing to deploy a test token on an unknown chain.`
    );
  }

  const [deployer] = await ethers.getSigners();
  if (!deployer) {
    throw new Error(
      "No deployer signer is configured for this network. Set DEPLOYER_PRIVATE_KEY in your local .env file, then retry."
    );
  }
  const INITIAL_SUPPLY = ethers.parseEther("10000000"); // 10 million tokens, 18 decimals

  console.log(`\n⚠️  TESTNET / DEVELOPMENT TOKEN DEPLOYMENT`);
  console.log(`Network  : ${network.name} (chain ID ${chainId})`);
  console.log(`Deployer : ${deployer.address}`);
  console.log(`Supply   : 10,000,000 TEST (18 decimals)\n`);

  const Token = await ethers.getContractFactory("TestERC20");
  const token = await Token.deploy("Test Token", "TEST", INITIAL_SUPPLY);
  await token.waitForDeployment();

  const tokenAddress = await token.getAddress();
  const txHash = token.deploymentTransaction()?.hash ?? null;

  console.log(`✅ TestERC20 deployed at: ${tokenAddress}`);
  console.log(`   Tx hash            : ${txHash}`);
  console.log(`   Explorer           : https://chainscan-galileo.0g.ai/address/${tokenAddress}`);
  console.log(`\nNext steps:`);
  console.log(`  1. Add to .env:  ESCROW_TOKEN_ADDRESS=${tokenAddress}`);
  console.log(`  2. Run:          npm run deploy:galileo`);

  // Write a small record so you don't lose the address between terminal sessions.
  const outDir = path.join(__dirname, "..", "deployments");
  fs.mkdirSync(outDir, { recursive: true });
  const outFile = path.join(outDir, `test-token-${network.name}.json`);
  fs.writeFileSync(
    outFile,
    JSON.stringify(
      {
        _warning: "TESTNET / DEVELOPMENT TOKEN — not a production asset",
        network: network.name,
        chainId,
        address: tokenAddress,
        txHash,
        deployer: deployer.address,
        name: "Test Token",
        symbol: "TEST",
        decimals: 18,
        initialSupply: INITIAL_SUPPLY.toString(),
        deployedAt: new Date().toISOString(),
      },
      null,
      2
    )
  );
  console.log(`\nRecord written to ${outFile}`);
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});

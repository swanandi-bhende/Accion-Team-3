import { HardhatUserConfig } from "hardhat/config";
import "@nomicfoundation/hardhat-toolbox";
import * as dotenv from "dotenv";

dotenv.config();

// ---------------------------------------------------------------------------
// Compiler note — pragma vs. evmVersion
// The contracts declare `pragma solidity ^0.8.20`. We keep the compiler at
// exactly 0.8.20 — no silent downgrade to 0.8.19.
//
// CONFLICT with 0G docs and resolution:
//   0G documentation recommends evmVersion: "cancun" for Galileo. However,
//   solc 0.8.20 does NOT support "cancun" — it only knows up to "shanghai"
//   (Cancun support was added in solc 0.8.24). Setting evmVersion: "cancun"
//   with solc 0.8.20 produces "Invalid EVM version requested" at compile time
//   (confirmed locally). We therefore set evmVersion: "shanghai".
//
// Why "shanghai" is correct for Galileo:
//   - Galileo's EVM fork is Cancun-era, which is a superset of Shanghai.
//   - Shanghai bytecode is fully backward-compatible on a Cancun chain;
//     every Shanghai opcode is also valid on Cancun.
//   - The only opcodes Cancun adds (TLOAD, TSTORE, MCOPY, BLOBHASH, etc.)
//     are not emitted by the contracts in this repo nor required by them.
//   - Using "shanghai" here makes the deployed bytecode identical to what
//     was verified in our local Hardhat tests, which also run on a Cancun-
//     aware EVM. No business logic changes.
//
// For verification on Chainscan: submit with evmVersion "shanghai" and
//   Solidity 0.8.20 — the bytecode will match exactly.
// ---------------------------------------------------------------------------
const GALILEO_CHAIN_ID = 16602;

const config: HardhatUserConfig = {
  solidity: {
    version: "0.8.20",
    settings: {
      optimizer: { enabled: true, runs: 200 },
      // "shanghai" is the highest EVM version supported by solc 0.8.20 and is
      // backward-compatible with Galileo's Cancun-era EVM.
      evmVersion: "shanghai",
    },
  },

  networks: {
    hardhat: {},
    localhost: {
      // LOCAL_RPC_URL lets the docker-compose deploy service reach hardhat-node.
      url: process.env.LOCAL_RPC_URL || "http://127.0.0.1:8545",
    },
    // Testnet/mainnet entries populated from .env when deploying beyond local dev
    sepolia: {
      url: process.env.SEPOLIA_RPC_URL || "",
      chainId: 11155111,
      accounts: process.env.DEPLOYER_PRIVATE_KEY ? [process.env.DEPLOYER_PRIVATE_KEY] : [],
    },
    // -----------------------------------------------------------------------
    // 0G Galileo Testnet — chain ID 16602
    // RPC:      https://evmrpc-testnet.0g.ai  (official public endpoint)
    // Explorer: https://chainscan-galileo.0g.ai
    // Gas token: 0G (native; obtained from the 0G faucet)
    // -----------------------------------------------------------------------
    galileo: {
      url: process.env.OG_RPC_URL || "https://evmrpc-testnet.0g.ai",
      chainId: GALILEO_CHAIN_ID,
      accounts: process.env.DEPLOYER_PRIVATE_KEY ? [process.env.DEPLOYER_PRIVATE_KEY] : [],
    },
  },
  // Source verification configuration.
  // Sepolia uses the standard Etherscan key from ETHERSCAN_API_KEY.
  // Galileo's Chainscan is Etherscan-API-compatible; its open API does not
  // require a paid key — the placeholder "galileo" is accepted.
  // Source: https://chainscan-galileo.0g.ai (Etherscan-fork explorer)
  etherscan: {
    apiKey: {
      sepolia: process.env.ETHERSCAN_API_KEY || "",
      galileo: process.env.OG_EXPLORER_API_KEY || "galileo",
    },
    customChains: [
      {
        network: "galileo",
        chainId: GALILEO_CHAIN_ID,
        urls: {
          // Etherscan-compatible API endpoint for source submission and query.
          apiURL: "https://chainscan-galileo.0g.ai/open/api",
          browserURL: "https://chainscan-galileo.0g.ai",
        },
      },
    ],
  },
  sourcify: { enabled: false },
  paths: {
    sources: "./contracts",
    tests: "./test",
    scripts: "./scripts",
  },
};

export default config;

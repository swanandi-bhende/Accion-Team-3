require("@nomicfoundation/hardhat-toolbox");

// Local chain that runs Team A's *deployed* bytecode (installed by
// scripts/ops/install_team_a_local.py with hardhat_setCode), so nothing here is
// compiled from Solidity. There is deliberately no `solidity` source path.
//
// The chain id matches 0G Galileo (16602), which means WEB3_CHAIN_ID=16602 is
// correct locally and on the real network: the same environment profile works
// against both.
module.exports = {
  networks: {
    hardhat: {
      chainId: 16602,
      // Generous gas limit so Team A's contracts execute unchanged.
      blockGasLimit: 30_000_000,
      allowUnlimitedContractSize: false,
    },
    localhost: {
      // Compose sets WEB3_RPC_URL=http://hardhat-node:8545 so the installer
      // reaches the node container instead of its own loopback.
      url: process.env.WEB3_RPC_URL || "http://127.0.0.1:8545",
    },
  },
};

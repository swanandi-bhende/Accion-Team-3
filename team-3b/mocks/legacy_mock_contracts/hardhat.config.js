require("@nomicfoundation/hardhat-toolbox");

/** @type import('hardhat/config').HardhatUserConfig */
module.exports = {
  solidity: "0.8.19",
  networks: {
    hardhat: {
      chainId: 31337
    },
    localhost: {
      // Compose sets WEB3_RPC_URL=http://hardhat-node:8545 so the deploy
      // container reaches the node container instead of its own loopback.
      url: process.env.WEB3_RPC_URL || "http://127.0.0.1:8545"
    }
  }
};

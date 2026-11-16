import { expect } from "chai";
import { ethers } from "hardhat";
import { loadFixture } from "@nomicfoundation/hardhat-toolbox/network-helpers";

describe("IdentityContract", function () {
  async function deployFixture() {
    const [owner, alice, bob] = await ethers.getSigners();
    const Identity = await ethers.getContractFactory("IdentityContract");
    const identity = await Identity.deploy();
    return { identity, owner, alice, bob };
  }

  const hashA = ethers.keccak256(ethers.toUtf8Bytes("identity-metadata-a"));
  const hashB = ethers.keccak256(ethers.toUtf8Bytes("identity-metadata-b"));

  // --- Unit tests (3.3) ---

  it("registers an identity and emits IdentityRegistered", async function () {
    const { identity, alice } = await loadFixture(deployFixture);
    await expect(identity.connect(alice).register(hashA))
      .to.emit(identity, "IdentityRegistered")
      .withArgs(alice.address, hashA);
    expect(await identity.getIdentity(alice.address)).to.equal(hashA);
  });

  it("reverts duplicate registration with IdentityAlreadyExists", async function () {
    const { identity, alice } = await loadFixture(deployFixture);
    await identity.connect(alice).register(hashA);
    await expect(
      identity.connect(alice).register(hashB)
    ).to.be.revertedWithCustomError(identity, "IdentityAlreadyExists");
  });

  it("reverts update by an unregistered address with IdentityNotFound", async function () {
    const { identity, bob } = await loadFixture(deployFixture);
    await expect(
      identity.connect(bob).update(hashB)
    ).to.be.revertedWithCustomError(identity, "IdentityNotFound");
  });

  it("emits IdentityUpdated on a successful update", async function () {
    const { identity, alice } = await loadFixture(deployFixture);
    await identity.connect(alice).register(hashA);
    await expect(identity.connect(alice).update(hashB))
      .to.emit(identity, "IdentityUpdated")
      .withArgs(alice.address, hashB);
  });

  it("returns bytes32(0) for an unregistered wallet", async function () {
    const { identity, bob } = await loadFixture(deployFixture);
    expect(await identity.getIdentity(bob.address)).to.equal(ethers.ZeroHash);
    expect(await identity.isRegistered(bob.address)).to.equal(false);
  });

  it("keeps registrations independent across wallets", async function () {
    const { identity, alice, bob } = await loadFixture(deployFixture);
    await identity.connect(alice).register(hashA);
    await identity.connect(bob).register(hashB);
    expect(await identity.getIdentity(alice.address)).to.equal(hashA);
    expect(await identity.getIdentity(bob.address)).to.equal(hashB);
  });

  // --- Property tests (3.2) ---

  it("Property 4: identity registration round-trip returns the registered hash", async function () {
    const { identity, alice, bob } = await loadFixture(deployFixture);
    const cases: Array<[typeof alice, string]> = [
      [alice, hashA],
      [bob, hashB],
    ];
    for (const [signer, hash] of cases) {
      await identity.connect(signer).register(hash);
      expect(await identity.getIdentity(signer.address)).to.equal(hash);
    }
  });

  it("Property 5: identity uniqueness is enforced per wallet", async function () {
    const { identity, alice, bob } = await loadFixture(deployFixture);
    await identity.connect(alice).register(hashA);
    // A second registration from the same wallet always reverts, whatever the hash.
    for (const hash of [hashA, hashB, ethers.ZeroHash]) {
      await expect(
        identity.connect(alice).register(hash)
      ).to.be.revertedWithCustomError(identity, "IdentityAlreadyExists");
    }
    // A different wallet is unaffected.
    await expect(identity.connect(bob).register(hashA)).to.not.be.reverted;
  });

  it("Property 6: update overwrites the stored hash", async function () {
    const { identity, alice } = await loadFixture(deployFixture);
    await identity.connect(alice).register(hashA);

    const sequence = [hashB, ethers.ZeroHash, hashA];
    for (const hash of sequence) {
      await identity.connect(alice).update(hash);
      expect(await identity.getIdentity(alice.address)).to.equal(hash);
    }
  });

  it("Property 6 (edge): bytes32(0) is a legal metadata hash, registration state is separate", async function () {
    const { identity, alice } = await loadFixture(deployFixture);
    await identity.connect(alice).register(ethers.ZeroHash);

    // Stored hash is zero, but the wallet IS registered — so a second register must revert
    // and an update must succeed.
    expect(await identity.getIdentity(alice.address)).to.equal(ethers.ZeroHash);
    expect(await identity.isRegistered(alice.address)).to.equal(true);
    await expect(
      identity.connect(alice).register(hashA)
    ).to.be.revertedWithCustomError(identity, "IdentityAlreadyExists");
    await expect(identity.connect(alice).update(hashA)).to.not.be.reverted;
  });
});

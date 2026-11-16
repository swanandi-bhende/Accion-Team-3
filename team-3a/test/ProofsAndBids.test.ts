import { expect } from "chai";
import { ethers } from "hardhat";
import { loadFixture } from "@nomicfoundation/hardhat-toolbox/network-helpers";
import { deployStack, createTask } from "./fixtures";

/// Covers the Team 3b handshake (submitProofs), the on-chain bid flow Team 3c uses,
/// and permissionless settlement to the accepted worker.
describe("Proofs, bids and permissionless settlement", function () {
  const bounty = ethers.parseEther("100");
  const h = (s: string) => ethers.keccak256(ethers.toUtf8Bytes(s));
  const CID = "bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi";

  async function setup() {
    const ctx = await deployStack();
    const [, , , , , , , prover] = await ethers.getSigners();
    await ctx.taskManager.setProver(prover.address, true);
    return { ...ctx, prover };
  }

  const STATUS = { Open: 0n, InProgress: 1n, Completed: 2n, Settled: 3n };

  describe("prover authorisation", function () {
    it("only the owner can grant the prover role", async function () {
      const { taskManager, outsider } = await loadFixture(setup);
      await expect(taskManager.connect(outsider).setProver(outsider.address, true))
        .to.be.revertedWithCustomError(taskManager, "NotOwner");
    });

    it("rejects submitProofs from a non-prover", async function () {
      const { taskManager, creator, outsider } = await loadFixture(setup);
      const id = await createTask(taskManager, creator, bounty);
      await expect(
        taskManager.connect(outsider).submitProofs([id], [h("p")], [CID], [h("r")])
      ).to.be.revertedWithCustomError(taskManager, "NotProver");
    });

    it("revoking the role blocks further submissions", async function () {
      const { taskManager, creator, prover } = await loadFixture(setup);
      const id = await createTask(taskManager, creator, bounty);
      await taskManager.setProver(prover.address, false);
      await expect(
        taskManager.connect(prover).submitProofs([id], [h("p")], [CID], [h("r")])
      ).to.be.revertedWithCustomError(taskManager, "NotProver");
    });
  });

  describe("submitProofs", function () {
    it("stores the proof, emits ProofSubmitted and completes the task", async function () {
      const { taskManager, creator, prover } = await loadFixture(setup);
      const id = await createTask(taskManager, creator, bounty);
      await expect(taskManager.connect(prover).submitProofs([id], [h("p")], [CID], [h("r")]))
        .to.emit(taskManager, "ProofSubmitted")
        .withArgs(id, h("p"), CID, h("r"), prover.address);

      expect(await taskManager.getStatus(id)).to.equal(STATUS.Completed);
      const rec = await taskManager.getProof(id);
      expect(rec.proofHash).to.equal(h("p"));
      expect(rec.modelCid).to.equal(CID);
      expect(rec.resultHash).to.equal(h("r"));
      expect(rec.submitter).to.equal(prover.address);
    });

    it("returns the accepted count", async function () {
      const { taskManager, creator, prover } = await loadFixture(setup);
      const a = await createTask(taskManager, creator, bounty, "a");
      const b = await createTask(taskManager, creator, bounty, "b");
      const n = await taskManager
        .connect(prover)
        .submitProofs.staticCall([a, b], [h("1"), h("2")], [CID, CID], [h("x"), h("y")]);
      expect(n).to.equal(2n);
    });

    it("skips bad items with ProofRejected instead of reverting the batch", async function () {
      const { taskManager, creator, prover } = await loadFixture(setup);
      const good = await createTask(taskManager, creator, bounty, "good");
      const dup = await createTask(taskManager, creator, bounty, "dup");
      const empty = await createTask(taskManager, creator, bounty, "empty");
      const unknown = h("never-created");

      await taskManager.connect(prover).submitProofs([dup], [h("d")], [CID], [h("dr")]);

      const tx = taskManager.connect(prover).submitProofs(
        [good, unknown, dup, empty],
        [h("g"), h("u"), h("d2"), ethers.ZeroHash],
        [CID, CID, CID, CID],
        [h("gr"), h("ur"), h("dr2"), h("er")]
      );
      await expect(tx).to.emit(taskManager, "ProofSubmitted").withArgs(good, h("g"), CID, h("gr"), prover.address);
      await expect(tx).to.emit(taskManager, "ProofRejected").withArgs(unknown, 1);
      await expect(tx).to.emit(taskManager, "ProofRejected").withArgs(dup, 3);
      await expect(tx).to.emit(taskManager, "ProofRejected").withArgs(empty, 4);

      expect(await taskManager.getStatus(good)).to.equal(STATUS.Completed);
      expect(await taskManager.getStatus(empty)).to.equal(STATUS.Open);
    });

    it("rejects proofs for a task that is already Settled (reason 2)", async function () {
      const ctx = await loadFixture(setup);
      const { taskManager, creator, prover, escrow, settlement } = ctx;
      const id = await createTask(taskManager, creator, bounty);
      await escrow.connect(creator).deposit(id, bounty);
      await taskManager.connect(creator).markComplete(id);
      await settlement.settle(id, ctx.worker.address);
      await expect(taskManager.connect(prover).submitProofs([id], [h("p")], [CID], [h("r")]))
        .to.emit(taskManager, "ProofRejected").withArgs(id, 2);
    });

    it("reverts when the arrays differ in length", async function () {
      const { taskManager, creator, prover } = await loadFixture(setup);
      const id = await createTask(taskManager, creator, bounty);
      await expect(
        taskManager.connect(prover).submitProofs([id], [h("p"), h("q")], [CID], [h("r")])
      ).to.be.revertedWithCustomError(taskManager, "ArrayLengthMismatch");
    });

    it("enforces the batch size limit of 50", async function () {
      const { taskManager, prover } = await loadFixture(setup);
      expect(await taskManager.MAX_PROOF_BATCH()).to.equal(50n);
      const mk = (n: number, v: any) => Array(n).fill(v);
      await expect(
        taskManager.connect(prover).submitProofs(mk(51, h("t")), mk(51, h("p")), mk(51, CID), mk(51, h("r")))
      ).to.be.revertedWithCustomError(taskManager, "BatchTooLarge");
      // exactly 50 is allowed (all rejected as unknown, but no revert)
      await taskManager.connect(prover).submitProofs(mk(50, h("t")), mk(50, h("p")), mk(50, CID), mk(50, h("r")));
    });

    it("an empty batch is a no-op", async function () {
      const { taskManager, prover } = await loadFixture(setup);
      expect(await taskManager.connect(prover).submitProofs.staticCall([], [], [], [])).to.equal(0n);
    });
  });

  describe("bids", function () {
    it("lets an agent bid and the creator accept, assigning the worker", async function () {
      const { taskManager, creator, worker } = await loadFixture(setup);
      const id = await createTask(taskManager, creator, bounty);
      await expect(taskManager.connect(worker).placeBid(id, ethers.parseEther("90"), 600, h("cap")))
        .to.emit(taskManager, "BidPlaced")
        .withArgs(id, worker.address, ethers.parseEther("90"), 600, h("cap"));
      const bid = await taskManager.getBid(id, worker.address);
      expect(bid.exists).to.equal(true);
      expect(bid.price).to.equal(ethers.parseEther("90"));

      await expect(taskManager.connect(creator).acceptBid(id, worker.address))
        .to.emit(taskManager, "BidAccepted").withArgs(id, worker.address);
      expect(await taskManager.taskWorker(id)).to.equal(worker.address);
      expect(await taskManager.getStatus(id)).to.equal(STATUS.InProgress);
    });

    it("rejects duplicate bids, unknown tasks and non-creator acceptance", async function () {
      const { taskManager, creator, worker, outsider } = await loadFixture(setup);
      const id = await createTask(taskManager, creator, bounty);
      await taskManager.connect(worker).placeBid(id, 1, 1, h("c"));
      await expect(taskManager.connect(worker).placeBid(id, 1, 1, h("c")))
        .to.be.revertedWithCustomError(taskManager, "BidAlreadyExists");
      await expect(taskManager.connect(worker).placeBid(h("nope"), 1, 1, h("c")))
        .to.be.revertedWithCustomError(taskManager, "TaskNotFound");
      await expect(taskManager.connect(outsider).acceptBid(id, worker.address))
        .to.be.revertedWithCustomError(taskManager, "NotTaskCreator");
      await expect(taskManager.connect(creator).acceptBid(id, outsider.address))
        .to.be.revertedWithCustomError(taskManager, "BidNotFound");
    });

    it("cannot bid on or re-accept a task that is no longer Open", async function () {
      const { taskManager, creator, worker, outsider } = await loadFixture(setup);
      const id = await createTask(taskManager, creator, bounty);
      await taskManager.connect(worker).placeBid(id, 1, 1, h("c"));
      await taskManager.connect(creator).acceptBid(id, worker.address);
      await expect(taskManager.connect(outsider).placeBid(id, 1, 1, h("c")))
        .to.be.revertedWithCustomError(taskManager, "InvalidStatusTransition");
      await expect(taskManager.connect(creator).acceptBid(id, worker.address))
        .to.be.revertedWithCustomError(taskManager, "InvalidStatusTransition");
    });
  });

  describe("end to end: create -> bid -> fund -> accept -> proof -> settleTask", function () {
    it("pays the accepted worker 81/9/10 with no owner involvement", async function () {
      const { taskManager, escrow, staking, settlement, token, creator, worker, stakerA, treasury, prover, outsider } =
        await loadFixture(setup);

      await staking.connect(stakerA).stake(ethers.parseEther("1000"));

      const id = await createTask(taskManager, creator, bounty);
      await taskManager.connect(worker).placeBid(id, bounty, 300, h("cap"));
      await escrow.connect(creator).deposit(id, bounty);
      await taskManager.connect(creator).acceptBid(id, worker.address);

      // Not settleable until the proof lands.
      await expect(settlement.connect(outsider).settleTask(id))
        .to.be.revertedWithCustomError(settlement, "TaskNotCompleted");

      await taskManager.connect(prover).submitProofs([id], [h("p")], [CID], [h("r")]);

      const w0 = await token.balanceOf(worker.address);
      const t0 = await token.balanceOf(treasury.address);
      await expect(settlement.connect(outsider).settleTask(id))
        .to.emit(settlement, "TaskSettled")
        .withArgs(id, worker.address, ethers.parseEther("81"), ethers.parseEther("9"), ethers.parseEther("10"));

      expect((await token.balanceOf(worker.address)) - w0).to.equal(ethers.parseEther("81"));
      expect((await token.balanceOf(treasury.address)) - t0).to.equal(ethers.parseEther("10"));
      expect(await taskManager.getStatus(id)).to.equal(STATUS.Settled);

      await expect(settlement.connect(outsider).settleTask(id)).to.be.reverted; // no double settle
    });

    it("settleTask reverts when no bid was accepted", async function () {
      const { taskManager, escrow, settlement, creator, prover, outsider } = await loadFixture(setup);
      const id = await createTask(taskManager, creator, bounty);
      await escrow.connect(creator).deposit(id, bounty);
      await taskManager.connect(prover).submitProofs([id], [h("p")], [CID], [h("r")]);
      await expect(settlement.connect(outsider).settleTask(id))
        .to.be.revertedWithCustomError(settlement, "NoWorkerAssigned");
    });

    it("settleTask reverts while the task is under-funded", async function () {
      const { taskManager, escrow, settlement, creator, worker, prover, outsider } = await loadFixture(setup);
      const id = await createTask(taskManager, creator, bounty);
      await taskManager.connect(worker).placeBid(id, 1, 1, h("c"));
      await taskManager.connect(creator).acceptBid(id, worker.address);
      await escrow.connect(creator).deposit(id, bounty / 2n);
      await taskManager.connect(prover).submitProofs([id], [h("p")], [CID], [h("r")]);
      await expect(settlement.connect(outsider).settleTask(id))
        .to.be.revertedWithCustomError(settlement, "InsufficientFunds");
    });

    it("owner-only settle() still works as the relayer fallback", async function () {
      const { taskManager, escrow, settlement, creator, worker, prover } = await loadFixture(setup);
      const id = await createTask(taskManager, creator, bounty);
      await escrow.connect(creator).deposit(id, bounty);
      await taskManager.connect(prover).submitProofs([id], [h("p")], [CID], [h("r")]);
      await expect(settlement.settle(id, worker.address)).to.emit(settlement, "TaskSettled");
    });
  });
});

describe("Artifact hash recording (Req 6.2)", function () {
  const bounty = ethers.parseEther("10");
  const CID = "QmYwAPJzv5CZsnA625s3Xf2nemtYgPpHdWEz79ojWnPbdG";

  async function setup() {
    const ctx = await deployStack();
    const [, , , , , , , prover] = await ethers.getSigners();
    await ctx.taskManager.setProver(prover.address, true);
    const id = await createTask(ctx.taskManager, ctx.creator, bounty);
    return { ...ctx, prover, id };
  }

  it("lets the creator, a prover and the accepted worker record a hash", async function () {
    const { taskManager, creator, prover, worker, id } = await loadFixture(setup);
    await expect(taskManager.connect(creator).recordArtifact(id, CID))
      .to.emit(taskManager, "ArtifactRecorded").withArgs(id, CID, creator.address);
    await taskManager.connect(prover).recordArtifact(id, CID + "2");
    expect(await taskManager.artifactHash(id)).to.equal(CID + "2");

    await taskManager.connect(worker).placeBid(id, 1, 1, ethers.ZeroHash);
    await taskManager.connect(creator).acceptBid(id, worker.address);
    await taskManager.connect(worker).recordArtifact(id, CID + "3");
    expect(await taskManager.artifactHash(id)).to.equal(CID + "3");
  });

  it("rejects strangers, unknown tasks and empty hashes", async function () {
    const { taskManager, creator, outsider, id } = await loadFixture(setup);
    await expect(taskManager.connect(outsider).recordArtifact(id, CID))
      .to.be.revertedWithCustomError(taskManager, "NotAuthorizedRecorder");
    await expect(taskManager.connect(creator).recordArtifact(ethers.keccak256("0x01"), CID))
      .to.be.revertedWithCustomError(taskManager, "TaskNotFound");
    await expect(taskManager.connect(creator).recordArtifact(id, ""))
      .to.be.revertedWithCustomError(taskManager, "EmptyContentHash");
  });
});

describe("submitProofs gas at the batch limit", function () {
  it("a full 50-item batch stays well under a block's gas limit", async function () {
    const ctx = await deployStack();
    const [, , , , , , , prover] = await ethers.getSigners();
    await ctx.taskManager.setProver(prover.address, true);
    const ids: string[] = [];
    for (let i = 0; i < 50; i++) {
      ids.push(await createTask(ctx.taskManager, ctx.creator, ethers.parseEther("1"), `g${i}`));
    }
    const h = (s: string) => ethers.keccak256(ethers.toUtf8Bytes(s));
    const cid = "bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi";
    const tx = await ctx.taskManager.connect(prover).submitProofs(
      ids, ids.map((_, i) => h(`p${i}`)), ids.map(() => cid), ids.map((_, i) => h(`r${i}`))
    );
    const gas = (await tx.wait())!.gasUsed;
    console.log("      gas for 50 proofs:", gas.toString());
    expect(gas).to.be.lessThan(10_000_000n);
  });
});

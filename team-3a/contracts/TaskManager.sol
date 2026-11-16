// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./interfaces/ITaskManager.sol";

/// @title TaskManager
/// @notice Creates on-chain task records with bounties and tracks their status (Requirement 4).
/// @dev The settlement address is wired post-deployment via `setSettlementContract`, mirroring
/// the one-time-setter pattern already used by EscrowContract for the same circular
/// deploy-order dependency.
contract TaskManager is ITaskManager {


    address public owner;
    address public settlementContract;

    /// @dev Monotonic counter; the sole source of taskId uniqueness.
    uint256 public taskCount;

    /// @notice Hard cap on `submitProofs` batch size (Team 3b is told this number).
    uint256 public constant MAX_PROOF_BATCH = 50;

    mapping(bytes32 => Task) private _tasks;

    /// @dev Addresses allowed to call `submitProofs` (Team 3b's funded signer).
    mapping(address => bool) public isProver;

    /// @inheritdoc ITaskManager
    mapping(bytes32 => address) public taskWorker;

    struct ProofRecord {
        bytes32 proofHash;
        string modelCid;
        bytes32 resultHash;
        address submitter;
        uint64 submittedAt;
    }

    struct Bid {
        uint256 price;
        uint64 etaSeconds;
        bytes32 capabilityProofHash;
        bool exists;
    }

    /// @notice Latest artifact content hash per task (Req 6.2); history is in ArtifactRecorded.
    mapping(bytes32 => string) public artifactHash;

    mapping(bytes32 => ProofRecord) private _proofs;
    mapping(bytes32 => mapping(address => Bid)) private _bids;

    event ProverUpdated(address indexed prover, bool allowed);
    event TaskCompleted(bytes32 indexed taskId);
    event TaskSettledStatus(bytes32 indexed taskId);

    error TaskNotFound();
    error NotTaskCreator();
    error InvalidStatusTransition();
    error NotProver();
    error NotOwner();
    error ArrayLengthMismatch();
    error BatchTooLarge();
    error BidNotFound();
    error BidAlreadyExists();
    error ZeroAddress();
    error NotAuthorizedRecorder();
    error EmptyContentHash();

    modifier onlySettlement() {
        require(msg.sender == settlementContract, "TaskManager: caller is not SettlementContract");
        _;
    }

    constructor() {
        owner = msg.sender;
    }

    /// @notice One-time wiring of the SettlementContract address (deploy-order dependency).
    function setSettlementContract(address _settlement) external {
        require(msg.sender == owner, "TaskManager: caller is not owner");
        require(settlementContract == address(0), "TaskManager: already set");
        settlementContract = _settlement;
    }

    /// @inheritdoc ITaskManager
    function createTask(
        bytes32 descriptionHash,
        uint256 deadline,
        uint256 bountyAmount
    ) external returns (bytes32 taskId) {
        if (deadline <= block.timestamp) revert InvalidDeadline();
        if (bountyAmount == 0) revert InvalidBountyAmount();

        // taskCount guarantees uniqueness; creator and hash are mixed in so ids are not
        // trivially guessable from the counter alone.
        taskId = keccak256(abi.encode(msg.sender, taskCount, descriptionHash));
        taskCount += 1;

        _tasks[taskId] = Task({
            creator: msg.sender,
            descriptionHash: descriptionHash,
            deadline: deadline,
            bountyAmount: bountyAmount,
            status: TaskStatus.Open
        });

        emit TaskCreated(taskId, msg.sender, descriptionHash, deadline, bountyAmount);
    }

    /// @notice Grants or revokes the right to submit proofs. Owner-only.
    function setProver(address prover, bool allowed) external {
        if (msg.sender != owner) revert NotOwner();
        if (prover == address(0)) revert ZeroAddress();
        isProver[prover] = allowed;
        emit ProverUpdated(prover, allowed);
    }

    /// @notice Anchors a batch of execution proofs (Team 3b -> Team 3a handshake).
    /// @dev Parallel arrays; all four must be the same length and at most MAX_PROOF_BATCH.
    /// Per-item problems never revert the batch: the item is skipped and ProofRejected is
    /// emitted, so one bad proof cannot block good ones in the same retry. A valid proof
    /// is stored and moves the task to Completed (settleable). Returns the accepted count.
    function submitProofs(
        bytes32[] calldata taskIds,
        bytes32[] calldata proofHashes,
        string[] calldata modelCids,
        bytes32[] calldata resultHashes
    ) external returns (uint256 accepted) {
        if (!isProver[msg.sender]) revert NotProver();
        uint256 n = taskIds.length;
        if (proofHashes.length != n || modelCids.length != n || resultHashes.length != n) {
            revert ArrayLengthMismatch();
        }
        if (n > MAX_PROOF_BATCH) revert BatchTooLarge();

        for (uint256 i = 0; i < n; i++) {
            uint8 reason = _recordProof(taskIds[i], proofHashes[i], modelCids[i], resultHashes[i]);
            if (reason == 0) {
                accepted++;
                emit ProofSubmitted(taskIds[i], proofHashes[i], modelCids[i], resultHashes[i], msg.sender);
            } else {
                emit ProofRejected(taskIds[i], reason);
            }
        }
    }

    function _recordProof(
        bytes32 taskId,
        bytes32 proofHash,
        string calldata modelCid,
        bytes32 resultHash
    ) private returns (uint8) {
        Task storage task = _tasks[taskId];
        if (task.creator == address(0)) return 1;
        if (_proofs[taskId].submittedAt != 0) return 3;
        if (task.status != TaskStatus.Open && task.status != TaskStatus.InProgress) return 2;
        if (proofHash == bytes32(0) || resultHash == bytes32(0) || bytes(modelCid).length == 0) return 4;

        _proofs[taskId] = ProofRecord(proofHash, modelCid, resultHash, msg.sender, uint64(block.timestamp));
        task.status = TaskStatus.Completed;
        emit TaskCompleted(taskId);
        return 0;
    }

    /// @notice Records the content hash of a stored artifact (called after the Storage
    /// Integration Module uploads it). Allowed for the task creator, the accepted worker,
    /// or a prover. Overwrites the latest hash; every call is kept in the event log.
    function recordArtifact(bytes32 taskId, string calldata contentHash) external {
        Task storage task = _tasks[taskId];
        if (task.creator == address(0)) revert TaskNotFound();
        if (
            msg.sender != task.creator &&
            msg.sender != taskWorker[taskId] &&
            !isProver[msg.sender]
        ) revert NotAuthorizedRecorder();
        if (bytes(contentHash).length == 0) revert EmptyContentHash();

        artifactHash[taskId] = contentHash;
        emit ArtifactRecorded(taskId, contentHash, msg.sender);
    }

    function getProof(bytes32 taskId) external view returns (ProofRecord memory) {
        return _proofs[taskId];
    }

    /// @notice An agent bids on an Open task. One bid per (task, bidder); price is in
    /// escrow-token units.
    function placeBid(
        bytes32 taskId,
        uint256 price,
        uint64 etaSeconds,
        bytes32 capabilityProofHash
    ) external {
        Task storage task = _tasks[taskId];
        if (task.creator == address(0)) revert TaskNotFound();
        if (task.status != TaskStatus.Open) revert InvalidStatusTransition();
        if (_bids[taskId][msg.sender].exists) revert BidAlreadyExists();

        _bids[taskId][msg.sender] = Bid(price, etaSeconds, capabilityProofHash, true);
        emit BidPlaced(taskId, msg.sender, price, etaSeconds, capabilityProofHash);
    }

    /// @notice Creator accepts a bid: the bidder becomes the task's worker and the task
    /// moves Open -> InProgress. This is how settlement later learns who to pay.
    function acceptBid(bytes32 taskId, address bidder) external {
        Task storage task = _tasks[taskId];
        if (task.creator == address(0)) revert TaskNotFound();
        if (msg.sender != task.creator) revert NotTaskCreator();
        if (task.status != TaskStatus.Open) revert InvalidStatusTransition();
        if (!_bids[taskId][bidder].exists) revert BidNotFound();

        taskWorker[taskId] = bidder;
        task.status = TaskStatus.InProgress;
        emit BidAccepted(taskId, bidder);
    }

    function getBid(bytes32 taskId, address bidder) external view returns (Bid memory) {
        return _bids[taskId][bidder];
    }

    /// @notice Marks a task complete so it becomes settleable. Creator-only.
    function markComplete(bytes32 taskId) external {
        Task storage task = _tasks[taskId];
        if (task.creator == address(0)) revert TaskNotFound();
        if (msg.sender != task.creator) revert NotTaskCreator();
        if (task.status != TaskStatus.Open && task.status != TaskStatus.InProgress) {
            revert InvalidStatusTransition();
        }

        task.status = TaskStatus.Completed;
        emit TaskCompleted(taskId);
    }

    /// @notice Marks a task settled. Restricted to SettlementContract; blocks double settlement.
    function markSettled(bytes32 taskId) external onlySettlement {
        Task storage task = _tasks[taskId];
        if (task.creator == address(0)) revert TaskNotFound();
        if (task.status != TaskStatus.Completed) revert InvalidStatusTransition();

        task.status = TaskStatus.Settled;
        emit TaskSettledStatus(taskId);
    }

    function getTask(bytes32 taskId) external view returns (Task memory) {
        return _tasks[taskId];
    }

    function getStatus(bytes32 taskId) external view returns (TaskStatus) {
        return _tasks[taskId].status;
    }
}

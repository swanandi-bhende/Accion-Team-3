// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title ITaskManager
/// @notice Interface for TaskManager, implementing Requirement 4 (Task and Bounty Creation).
interface ITaskManager {
    enum TaskStatus { Open, InProgress, Completed, Settled }

    struct Task {
        address creator;
        bytes32 descriptionHash;
        uint256 deadline;
        uint256 bountyAmount;
        TaskStatus status;
    }

    event TaskCreated(
        bytes32 indexed taskId,
        address indexed creator,
        bytes32 descriptionHash,
        uint256 deadline,
        uint256 bountyAmount
    );

    /// @notice Emitted for every proof accepted by `submitProofs` (Team 3b consumes this).
    event ProofSubmitted(
        bytes32 indexed taskId,
        bytes32 proofHash,
        string modelCid,
        bytes32 resultHash,
        address indexed submitter
    );

    /// @notice Emitted instead of reverting when one item of a proof batch is invalid.
    /// reason: 1 = unknown task, 2 = task not Open/InProgress, 3 = proof already submitted,
    ///         4 = empty proofHash / resultHash / modelCid.
    event ProofRejected(bytes32 indexed taskId, uint8 reason);

    event BidPlaced(
        bytes32 indexed taskId,
        address indexed bidder,
        uint256 price,
        uint64 etaSeconds,
        bytes32 capabilityProofHash
    );
    event BidAccepted(bytes32 indexed taskId, address indexed worker);

    /// @notice Content hash (CID) of a stored model artifact, recorded for a task (Req 6.2).
    event ArtifactRecorded(bytes32 indexed taskId, string contentHash, address indexed recorder);

    error InvalidDeadline();
    error InvalidBountyAmount();

    /// @notice Creates a task record. Reverts InvalidDeadline for past deadlines,
    /// InvalidBountyAmount for a zero bounty. Returns a unique taskId.
    function createTask(
        bytes32 descriptionHash,
        uint256 deadline,
        uint256 bountyAmount
    ) external returns (bytes32 taskId);

    /// @notice Worker assigned through `acceptBid`; zero until a bid is accepted.
    function taskWorker(bytes32 taskId) external view returns (address);

    function markSettled(bytes32 taskId) external;
    function getStatus(bytes32 taskId) external view returns (TaskStatus);
    function getTask(bytes32 taskId) external view returns (Task memory);
}

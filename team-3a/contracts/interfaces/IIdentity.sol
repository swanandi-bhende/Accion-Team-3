// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title IIdentity
/// @notice Interface for IdentityContract, implementing Requirement 2 (On-Chain Identity Registration).
interface IIdentity {
    event IdentityRegistered(address indexed wallet, bytes32 metadataHash);
    event IdentityUpdated(address indexed wallet, bytes32 metadataHash);

    error IdentityAlreadyExists();
    error IdentityNotFound();

    /// @notice Registers the caller with `metadataHash`. Reverts IdentityAlreadyExists
    /// if the caller already holds a registration.
    function register(bytes32 metadataHash) external;

    /// @notice Overwrites the caller's stored metadata hash. Reverts IdentityNotFound
    /// if the caller has never registered.
    function update(bytes32 newMetadataHash) external;

    /// @notice Returns the stored metadata hash for `wallet`, or bytes32(0) if unregistered.
    function getIdentity(address wallet) external view returns (bytes32);

    /// @notice Whether `wallet` holds a registration. Distinct from getIdentity, since
    /// bytes32(0) is itself a legal metadata hash.
    function isRegistered(address wallet) external view returns (bool);
}

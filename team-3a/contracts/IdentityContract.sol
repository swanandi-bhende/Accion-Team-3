// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./interfaces/IIdentity.sol";

/// @title IdentityContract
/// @notice On-chain identity registration for platform participants (Requirement 2).
/// @dev Registration state is tracked in a separate `_registered` mapping rather than
/// inferred from a non-zero metadata hash. bytes32(0) is a legal hash value, so
/// `_metadata[wallet] != 0` would wrongly report a participant who registered a zero
/// hash as unregistered, and would let them register a second time.
contract IdentityContract is IIdentity {
    mapping(address => bytes32) private _metadata;
    mapping(address => bool) private _registered;

    /// @inheritdoc IIdentity
    function register(bytes32 metadataHash) external {
        if (_registered[msg.sender]) revert IdentityAlreadyExists();

        _registered[msg.sender] = true;
        _metadata[msg.sender] = metadataHash;

        emit IdentityRegistered(msg.sender, metadataHash);
    }

    /// @inheritdoc IIdentity
    function update(bytes32 newMetadataHash) external {
        if (!_registered[msg.sender]) revert IdentityNotFound();

        _metadata[msg.sender] = newMetadataHash;

        emit IdentityUpdated(msg.sender, newMetadataHash);
    }

    /// @inheritdoc IIdentity
    function getIdentity(address wallet) external view returns (bytes32) {
        return _metadata[wallet];
    }

    /// @inheritdoc IIdentity
    function isRegistered(address wallet) external view returns (bool) {
        return _registered[wallet];
    }
}

# Copyright (c) 2026 Zhambyl Yermagambet
"""Check stored package declarations and settings without executing feature code."""

import sqlite3

from baqylau_extension_api.manifest.activation import activation_order
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.schemas import SchemaSet

from extensions.models import lifecycle_operations as operations
from extensions.models.lifecycle_state import LifecycleState
from extensions.models.runtime_candidates import MigratingRuntimePackage, RuntimeCandidate, RuntimePackageCandidate
from extensions.models.settings import SettingsOverrides, capture_settings


def validate_proposal(
    connection: sqlite3.Connection, lifecycle_state: LifecycleState, proposal: operations.LifecycleProposal,
) -> None:
    """Validate candidate order and settings against retained package declarations."""
    manifests = tuple(
        retained_manifest(connection, package.extension_info.package_digest) for package in proposal.candidate.packages
    )
    _require_order(proposal.candidate, manifests)
    schemas = SchemaSet(tuple(schema for manifest in manifests for schema in manifest.schemas))
    for package, manifest in zip(proposal.candidate.packages, manifests, strict=True):
        package.validate_manifest(manifest, schemas)
        _require_captured_settings(lifecycle_state, proposal, package, manifest)
    _validate_intents(connection, proposal)


def retained_manifest(connection: sqlite3.Connection, package_digest: str) -> ExtensionManifest:
    """Read a validated captured declaration, not a mutable discovery source.

    Returns:
        The retained package declaration required by this operation.

    Raises:
        ValueError: If no checked manifest exists for the selected digest.

    """
    row = connection.execute(
        "SELECT manifest FROM extension_package_manifests WHERE package_digest=?", (package_digest,),
    ).fetchone()
    if row is None:
        message = "lifecycle selection requires a retained package manifest"
        raise ValueError(message)
    return ExtensionManifest.model_validate_json(str(row["manifest"]))


def selected_overrides(
    lifecycle_state: LifecycleState, proposal: operations.LifecycleProposal, owner: str,
) -> SettingsOverrides:
    """Apply a proposed raw settings change without writing it to current storage.

    Returns:
        The proposed override, accepted prior override, or initial empty choice.

    """
    for change in proposal.settings_changes:
        if change.extension_id == owner:
            return change.settings
    return next((
        entry.settings for entry in lifecycle_state.settings if entry.extension_id == owner
    ), SettingsOverrides())


def _require_captured_settings(
    lifecycle_state: LifecycleState, proposal: operations.LifecycleProposal,
    package: RuntimePackageCandidate, manifest: ExtensionManifest,
) -> None:
    overrides = selected_overrides(lifecycle_state, proposal, manifest.extension_id)
    if isinstance(package, MigratingRuntimePackage):
        if package.source != overrides or any(
            change.extension_id == manifest.extension_id for change in proposal.settings_changes
        ):
            message = "migration source does not match the exact accepted raw settings"
            raise ValueError(message)
        return
    if package.settings != capture_settings(manifest, overrides):
        message = "runtime settings do not match the selected stored overrides"
        raise ValueError(message)


def _validate_intents(connection: sqlite3.Connection, proposal: operations.LifecycleProposal) -> None:
    invalid = any(
        intent.package_digest is not None
        and retained_manifest(connection, intent.package_digest).extension_id != intent.extension_id
        for intent in proposal.intents
    )
    if invalid:
        message = "extension intent digest belongs to another owner"
        raise ValueError(message)
    for intent in proposal.intents:
        matching = tuple(
            package.extension_info.package_digest for package in proposal.candidate.packages
            if package.extension_info.extension_id == intent.extension_id
        )
        expected = (intent.package_digest,) if intent.enabled else ()
        if matching != expected:
            message = "requested enable state does not match the candidate selection"
            raise ValueError(message)


def _require_order(candidate: RuntimeCandidate, manifests: tuple[ExtensionManifest, ...]) -> None:
    order = tuple(package.extension_info.extension_id for package in candidate.packages)
    if order != activation_order(manifests):
        message = "stored runtime candidate must use the checked activation order"
        raise ValueError(message)

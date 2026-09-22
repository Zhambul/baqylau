# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate raw settings changes before reserving a lifecycle operation."""

import sqlite3

from baqylau_extension_api.operations.documents import validate_settings
from baqylau_extension_api.schemas import SchemaSet

from extensions.models.lifecycle_operations import LifecycleProposal
from extensions.models.lifecycle_selection import SettingsChange
from extensions.models.lifecycle_state import LifecycleState
from repository.impl.sqlite.extension_lifecycle_validation import retained_manifest


def settings_are_current(lifecycle_state: LifecycleState, proposal: LifecycleProposal) -> bool:
    """Compare all changed owner revisions at the same lifecycle read boundary.

    Returns:
        True only when every raw override still has the selected revision.

    """
    return all(
        change.expected_revision == next((
            entry.settings.revision for entry in lifecycle_state.settings if entry.extension_id == change.extension_id
        ), 0) for change in proposal.settings_changes
    )


def validate_changes(connection: sqlite3.Connection, proposal: LifecycleProposal) -> None:
    """Check complete raw overrides, including settings for a disabled package.

    Raises:
        ValueError: If active data and settings select different package identities.

    """
    for change in proposal.settings_changes:
        _validate_change(connection, change)
        matching = tuple(
            package.extension_info.package_digest for package in proposal.candidate.packages
            if package.extension_info.extension_id == change.extension_id
        )
        if matching and matching != (change.package_digest,):
            message = "settings change and active candidate select different package digests"
            raise ValueError(message)


def _validate_change(connection: sqlite3.Connection, change: SettingsChange) -> None:
    manifest = retained_manifest(connection, change.package_digest)
    definition = manifest.settings
    if manifest.extension_id != change.extension_id or definition is None:
        message = "settings change requires the selected owner's settings declaration"
        raise ValueError(message)
    schemas = SchemaSet(manifest.schemas)
    if change.settings.installation is not None:
        if "installation" not in definition.scopes:
            message = "installation settings override is not declared"
            raise ValueError(message)
        validate_settings(manifest, change.settings.installation, schemas)
    if any(scoped.scope.kind not in definition.scopes for scoped in change.settings.scopes):
        message = "settings override scope is not declared"
        raise ValueError(message)
    for scoped in change.settings.scopes:
        validate_settings(manifest, scoped.settings, schemas)

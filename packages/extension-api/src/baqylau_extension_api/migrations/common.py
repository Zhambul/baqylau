# Copyright (c) 2026 Zhambyl Yermagambet
"""Apply shared migration ownership, binding, and encoded-size checks."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.migrations import DeclaredMigrationPath
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.base import WireModel
from baqylau_extension_api.models.migrations import MigrationBinding

MAX_MIGRATION_REQUEST_BYTES = 8_388_608
MAX_MIGRATION_RESULT_BYTES = 4_194_304


def require_declared_path(
    manifest: ExtensionManifest, binding: MigrationBinding, path: DeclaredMigrationPath,
) -> None:
    """Require an exact declared conversion, never an inferred upgrade or downgrade.

    Raises:
        ExtensionContractError: If owner, capability, or exact path is not declared.

    """
    if binding.extension_id != manifest.extension_id or "migrations" not in manifest.capabilities:
        message = "migration owner or capability is not declared"
        raise ExtensionContractError(message)
    if path not in manifest.migration_paths:
        message = "the exact migration path is not declared"
        raise ExtensionContractError(message)


def require_migration_binding(expected: MigrationBinding, actual: MigrationBinding) -> None:
    """Reject output for another candidate, call, owner, scope, or runtime.

    Raises:
        ExtensionContractError: If any part of the binding changed.

    """
    if actual != expected:
        message = "migration result does not match its candidate call"
        raise ExtensionContractError(message)


def require_encoded_limit(document: WireModel, limit: int) -> None:
    """Bound complete requests and replies, not only individual documents.

    Raises:
        ExtensionContractError: If the complete encoded message is too large.

    """
    if len(document.model_dump_json().encode("utf-8")) > limit:
        message = "migration exceeds its encoded size limit"
        raise ExtensionContractError(message)

# Copyright (c) 2026 Zhambyl Yermagambet
"""Check one committed runtime for original input, source progress, and interpretation writes."""

import sqlite3

from baqylau_extension_api.models.base import ExtensionId, Identifier
from baqylau_extension_api.schemas import SchemaSet

from extensions.models.lifecycle_selection import RuntimePackageSelection, RuntimeSelection
from extensions.models.processing_package import ProcessingPackage
from repository.impl.sqlite import extension_lifecycle_reads, extension_lifecycle_validation


def require_runtime(
    connection: sqlite3.Connection, manager_id: Identifier, runtime_revision: Identifier,
) -> RuntimeSelection:
    """Reject late output from another manager or runtime under the write lock.

    Returns:
        The complete committed selection.

    Raises:
        ValueError: If the manager or runtime is no longer current.
        TypeError: If the runtime is not a complete ready selection.

    """
    head = connection.execute(
        "SELECT manager_id, committed_runtime FROM extension_lifecycle_head WHERE id=1",
    ).fetchone()
    current = None if head is None else (head["manager_id"], head["committed_runtime"])
    if current != (manager_id, runtime_revision):
        message = "processing runtime is not the committed runtime"
        raise ValueError(message)
    selected = extension_lifecycle_reads.read_runtime(connection, runtime_revision)
    if not isinstance(selected, RuntimeSelection):
        message = "processing runtime is not ready"
        raise TypeError(message)
    return selected


def require_package(
    connection: sqlite3.Connection, runtime: RuntimeSelection, extension_id: ExtensionId,
) -> ProcessingPackage:
    """Select only an active owner from the exact committed runtime.

    Returns:
        Its retained declaration, schemas, and captured settings.

    Raises:
        ValueError: If the source owner is not selected.

    """
    package = next((
        package for package in runtime.packages if package.extension_info.extension_id == extension_id
    ), None)
    if package is None:
        message = "processing owner is not enabled in the committed runtime"
        raise ValueError(message)
    return retained_package(connection, package)


def retained_package(connection: sqlite3.Connection, selected: RuntimePackageSelection) -> ProcessingPackage:
    """Read stored declarations instead of importing an extension or reading mutable source files.

    Returns:
        One checked data-only package description.

    """
    manifest = extension_lifecycle_validation.retained_manifest(connection, selected.extension_info.package_digest)
    schemas = SchemaSet(manifest.schemas)
    selected.validate_manifest(manifest, schemas)
    return ProcessingPackage(selected, manifest, schemas)

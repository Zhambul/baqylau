# Copyright (c) 2026 Zhambyl Yermagambet
"""Check source authority and captured settings under the repository write lock."""

import sqlite3

from baqylau_extension_api.sources import batches, registration

from extensions.models.source_reads import SourceReadProposal
from repository.impl.sqlite import processing_runtime


def validate_source_read(connection: sqlite3.Connection, proposal: SourceReadProposal) -> None:
    """Reject stale workers, changed settings, undeclared sources, and invalid documents.

    Raises:
        ValueError: If captured settings differ from the committed selection.

    """
    context = proposal.request.context
    runtime = processing_runtime.require_runtime(connection, proposal.manager_id, context.binding.runtime_revision)
    package = processing_runtime.require_package(connection, runtime, context.binding.extension_id)
    expected = package.selection.settings.for_scope(context.binding.scope)
    if context.settings_revision != package.selection.settings.revision or context.settings != expected:
        message = "source settings differ from the committed selection"
        raise ValueError(message)
    registration.validate_source_read(package.manifest, package.schemas, proposal.request)
    batches.validate_batch_documents(package.manifest, package.schemas, proposal.response)

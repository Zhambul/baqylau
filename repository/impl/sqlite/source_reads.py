# Copyright (c) 2026 Zhambyl Yermagambet
"""Expose one atomic source operation through its explicit repository protocol."""


from core.work_queue import WorkKind
from extensions.models.source_reads import SourceCheckpoint, SourceKey, SourceReadCommit, SourceReadOutcome
from repository.contract.source_reads import ExtensionSourceRepository
from repository.impl.sqlite import source_read_codec as codec, source_read_writes
from repository.impl.sqlite.connection import SqliteDatabase


class SqliteExtensionSourceRepository(ExtensionSourceRepository):
    """Retain source progress independently of worker and runtime replacement."""

    def __init__(self, sqlite_database: SqliteDatabase) -> None:
        """Use the existing database transaction and work notice boundary."""
        self.database = sqlite_database

    def source_checkpoint(self, key: SourceKey) -> SourceCheckpoint:
        """Capture exact committed progress before a source call.

        Returns:
            The full checkpoint, including an explicit initial revision.

        """
        checked = SourceKey.model_validate(key)
        with self.database.read() as connection:
            return codec.read_checkpoint(connection, checked)

    def record_source_read(self, request: SourceReadCommit) -> SourceReadOutcome:
        """Store complete original input and source progress, or change nothing.

        Returns:
            Accepted rows and progress for this exact call.

        """
        checked = SourceReadCommit.model_validate(request)
        notices = (WorkKind.RAW,) if checked.proposal.response.observations else ()
        with self.database.write(*notices, notify_readers=False) as connection:
            return source_read_writes.commit_read(connection, checked)

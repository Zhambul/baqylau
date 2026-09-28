# Copyright (c) 2026 Zhambyl Yermagambet
"""Read the newest lifecycle operations for the management history."""

from dataclasses import dataclass

from extensions.models.lifecycle_operations import LifecycleOperation
from repository.impl.sqlite import connection, extension_lifecycle_reads as reads


@dataclass(frozen=True)
class SqliteOperationHistory:
    """Read retained operations; no running manager is necessary."""

    database: connection.SqliteDatabase

    def recent(self, limit: int) -> tuple[LifecycleOperation, ...]:
        """Read the newest operations at one snapshot through the same validation as one read.

        Returns:
            At most `limit` operations, newest first.

        """
        with self.database.read() as connection_handle:
            rows = connection_handle.execute(
                "SELECT operation_id FROM extension_lifecycle_operations "
                "ORDER BY created_at DESC, operation_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            operations = (reads.read_operation(connection_handle, str(row["operation_id"])) for row in rows)
            return tuple(operation for operation in operations if operation is not None)

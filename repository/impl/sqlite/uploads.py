"""The record of what the browser attached.

The bytes are on disk because the harness is handed an `@path`. This is what
makes the directory prunable — `remove_expired` returns the rows so the caller
unlinks; a repository does not touch the filesystem.
"""

from __future__ import annotations

from domain.uploads import StoredUpload
from repository.contract.uploads import UploadRepository
from repository.impl.sqlite import rows
from repository.impl.sqlite.connection import SqliteDatabase
from repository.mapper import uploads as mapper


class SqliteUploadRepository(UploadRepository):
    def __init__(self, database: SqliteDatabase) -> None:
        self.database = database

    def record(self, upload: StoredUpload) -> None:
        with self.database.write() as connection:
            connection.execute(
                "INSERT INTO uploads(upload_id, session_id, name, media_type, byte_size, "
                "stored_path, created_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
                mapper.upload_values(upload),
            )

    def remove_expired(self, created_before: float) -> tuple[StoredUpload, ...]:
        with self.database.write() as connection:
            found = connection.execute(
                "SELECT * FROM uploads WHERE created_at < ?", (created_before,)
            ).fetchall()
            if found:
                connection.execute("DELETE FROM uploads WHERE created_at < ?", (created_before,))
        return tuple(mapper.stored_upload(rows.upload(row)) for row in found)

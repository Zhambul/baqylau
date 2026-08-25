"""SQLite automatic-title job queue."""

from __future__ import annotations

import sqlite3

from domain.ids import SessionId
from domain.naming import NamingJob, NamingJobState
from repository.impl.sqlite.connection import SqliteDatabase


class SqliteNamingJobRepository:
    def __init__(self, sqlite_database: SqliteDatabase) -> None:
        self.database = sqlite_database

    def enqueue(self, naming_job: NamingJob) -> bool:
        with self.database.write() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO naming_jobs(job_key, session_id, prompt, state) "
                "VALUES(?, ?, ?, 'pending')",
                (naming_job.key, str(naming_job.session_id), naming_job.prompt),
            )
        return cursor.rowcount == 1

    def register_running(self, naming_job: NamingJob) -> tuple[NamingJob, bool]:
        with self.database.write() as connection:
            inserted = connection.execute(
                "INSERT OR IGNORE INTO naming_jobs(job_key, session_id, prompt, state) "
                "VALUES(?, ?, ?, 'running')",
                (naming_job.key, str(naming_job.session_id), naming_job.prompt),
            )
            row = connection.execute(
                "SELECT * FROM naming_jobs WHERE job_key=?",
                (naming_job.key,),
            ).fetchone()
        if row is None:
            raise RuntimeError("naming job disappeared after insert")
        return _job(row), inserted.rowcount == 1

    def claim_next(self) -> NamingJob | None:
        with self.database.write() as connection:
            row = connection.execute(
                "SELECT * FROM naming_jobs WHERE state='pending' ORDER BY id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE naming_jobs SET state='running' WHERE job_key=? AND state='pending'",
                (row["job_key"],),
            )
        return _job(row, naming_job_state=NamingJobState.RUNNING)

    def complete(self, key: str, title: str) -> None:
        with self.database.write() as connection:
            connection.execute(
                "UPDATE naming_jobs SET state='completed', title=?, error=NULL WHERE job_key=?",
                (title, key),
            )

    def fail(self, key: str, reason: str) -> None:
        with self.database.write() as connection:
            connection.execute(
                "UPDATE naming_jobs SET state='failed', error=? WHERE job_key=?",
                (reason, key),
            )

    def find(self, key: str) -> NamingJob | None:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM naming_jobs WHERE job_key=?",
                (key,),
            ).fetchone()
        return _job(row) if row is not None else None


def _job(
    row: sqlite3.Row,
    *,
    naming_job_state: NamingJobState | None = None,
) -> NamingJob:
    return NamingJob(
        key=str(row["job_key"]),
        session_id=SessionId(str(row["session_id"])),
        prompt=str(row["prompt"]),
        state=naming_job_state or NamingJobState(str(row["state"])),
        title=str(row["title"]) if row["title"] is not None else None,
        error=str(row["error"]) if row["error"] is not None else None,
    )

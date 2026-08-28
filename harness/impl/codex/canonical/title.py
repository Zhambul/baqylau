# harness/impl/codex/canonical/title.py — codex SESSION TITLE (read + parked rename).
#
# A `NativeSessionTitleRepository`, and the ONE repository implementation that
# lives outside `repository/impl/` — because a shared package may not contain a
# harness's name, and this one is entirely about codex's own store.
#
# WHERE the title lives: codex keeps it in a per-home sqlite index,
# `$CODEX_HOME/state_<N>.sqlite` (or `~/.codex/state_<N>.sqlite`), table
# `threads`, columns `name` and `title`, keyed by the thread uuid (== the
# rollout's uuid == the session id for a standalone host). Current Codex uses
# `name` for both its generated short title and an explicit `/rename`. The
# store has no origin field. A Baqylau control record keeps a person-selected
# title separately, so native index observations use automatic precedence.
# Older indexes have only `title`.
# The numbered filename is VERSION-FRAGILE (state_5 on the dev machine,
# 2026-07), so it is resolved by globbing and taking the highest N. It is not
# ours: we do not create it, version it, or set a pragma on it.
#
# RENAME: set_title writes `threads.name` on the current schema and falls back
# to `threads.title` on the old schema. This is the PARKED path, the one the
# dashboard's web rename uses for a Codex session with nothing running to
# overwrite it. A LIVE rename is the controller's business (Codex's own
# `/rename`, pasted into the window), the same live-vs-parked split Claude has.
import glob
import os
import re
import sqlite3
from dataclasses import dataclass

from repository.contract.titles import NativeSessionTitleRepository
from harness.models import TitleWriteOutcome
from harness.impl.codex.canonical import rollout as RO
from domain.values import TitleOrigin

_UUID = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)
TITLE_HEAD_LINES = 200  # rollout head lines the first-prompt fallback scans
# The index is another product's file, reached on a user-facing request path.
# Fail fast rather than hold a request open behind codex's own writer.
CONNECT_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class CodexNativeTitle:
    text: str
    origin: TitleOrigin


@dataclass(frozen=True)
class CodexTitleStoreMarker:
    """The native index files that can change a thread title."""

    database: str
    database_state: tuple[int, int, int]
    write_ahead_state: tuple[int, int, int] | None


def _codex_directory(
    source_reference: str,
    configuration_directory: str,
) -> str:
    """Find the Codex home that owns one rollout.

    A daemon can observe sessions from more than one Codex home. The rollout
    path is the session-specific authority. The process environment is only a
    fallback for older or nonstandard source paths.
    """
    current = os.path.dirname(os.path.realpath(source_reference))
    while current and current != os.path.dirname(current):
        if os.path.basename(current) == "sessions":
            return os.path.dirname(current)
        current = os.path.dirname(current)
    return configuration_directory


def _state_database(source_reference: str, configuration_directory: str) -> str:
    """The newest codex `state_<N>.sqlite` index (highest N), or "" — resolved
    defensively because the numbered name drifts across codex versions."""
    codex_directory = _codex_directory(source_reference, configuration_directory)
    candidates = glob.glob(os.path.join(codex_directory, "state_*.sqlite"))
    best, best_number = "", -1
    for candidate in candidates:
        match = re.search(r"state_(\d+)\.sqlite$", os.path.basename(candidate))
        number = int(match.group(1)) if match else 0
        if number >= best_number:
            best, best_number = candidate, number
    if best:
        return best
    plain = os.path.join(codex_directory, "state.sqlite")
    return plain if os.path.isfile(plain) else ""


def title_store_marker(
    source_reference: str,
    configuration_directory: str,
) -> CodexTitleStoreMarker | None:
    """Return the state needed to skip an unchanged native title read."""
    database = _state_database(source_reference, configuration_directory)
    if not database:
        return None
    database_state = _file_marker(database)
    if database_state is None:
        return None
    return CodexTitleStoreMarker(
        database,
        database_state,
        _file_marker(f"{database}-wal"),
    )


def _file_marker(path: str) -> tuple[int, int, int] | None:
    try:
        status = os.stat(path)
    except OSError:
        return None
    return status.st_ino, status.st_mtime_ns, status.st_size


def _thread_uuid(path: str) -> str:
    """The thread uuid a rollout path names (== the session id for a standalone
    host), or "" — read out of the `rollout-<ts>-<uuid>.jsonl` filename."""
    match = _UUID.search(os.path.basename(path or ""))
    return match.group(1) if match else ""


class CodexThreadTitleRepository(NativeSessionTitleRepository):
    def __init__(self, configuration_directory: str) -> None:
        self.configuration_directory = configuration_directory

    def renameable(self, source_reference: str) -> bool:
        """True for a codex rollout this plugin owns — the gate both the
        dashboard's live rename and the parked write ask before naming a
        session. A standalone codex host's window carries the same session tag
        as a Claude one, so this is what keeps a Claude rename off it."""
        return bool(RO.owns(source_reference))

    def set_title(self, source_reference: str, title: str) -> TitleWriteOutcome:
        if not self.renameable(source_reference):
            return TitleWriteOutcome.UNSUPPORTED
        database = _state_database(
            source_reference,
            self.configuration_directory,
        )
        thread_uuid = _thread_uuid(source_reference)
        if not database or not thread_uuid:
            return TitleWriteOutcome.UNAVAILABLE
        try:
            connection = sqlite3.connect(database, timeout=CONNECT_TIMEOUT_SECONDS)
            try:
                column = "name" if _has_thread_name(connection) else "title"
                cursor = connection.execute(
                    f"UPDATE threads SET {column}=? WHERE id=?",
                    (title, thread_uuid),
                )
                connection.commit()
            finally:
                connection.close()
        except sqlite3.Error:
            # An index codex renamed, moved, or changed the shape of. The
            # caller reports a failed rename; it must not see a driver
            # exception from another product's file.
            return TitleWriteOutcome.UNAVAILABLE
        return TitleWriteOutcome.RENAMED if cursor.rowcount else TitleWriteOutcome.UNAVAILABLE

    def read_title(self, source_reference: str) -> CodexNativeTitle | None:
        """Read the current title from the native thread index."""
        if not self.renameable(source_reference):
            return None
        database = _state_database(
            source_reference,
            self.configuration_directory,
        )
        thread_uuid = _thread_uuid(source_reference)
        if not database or not thread_uuid:
            return None
        try:
            connection = sqlite3.connect(database, timeout=CONNECT_TIMEOUT_SECONDS)
            try:
                current_schema = _has_thread_name(connection)
                columns = "name, title" if current_schema else "title"
                row = connection.execute(
                    f"SELECT {columns} FROM threads WHERE id=?",
                    (thread_uuid,),
                ).fetchone()
            finally:
                connection.close()
        except sqlite3.Error:
            return None
        if not row:
            return None
        if current_schema:
            name = str(row[0]).strip() if row[0] is not None else ""
            if name:
                return CodexNativeTitle(name, TitleOrigin.AUTOMATIC)
            automatic = str(row[1]).strip() if row[1] is not None else ""
        else:
            automatic = str(row[0]).strip() if row[0] is not None else ""
        return (
            CodexNativeTitle(automatic, TitleOrigin.AUTOMATIC)
            if automatic
            else None
        )


def _has_thread_name(connection: sqlite3.Connection) -> bool:
    return any(
        str(row[1]) == "name"
        for row in connection.execute("PRAGMA table_info(threads)")
    )


titles = CodexThreadTitleRepository(os.path.expanduser("~/.codex"))

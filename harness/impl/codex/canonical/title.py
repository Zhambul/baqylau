# harness/impl/codex/canonical/title.py — codex SESSION TITLE (read + parked rename).
#
# A `NativeSessionTitleRepository`, and the ONE repository implementation that
# lives outside `repository/impl/` — because a shared package may not contain a
# harness's name, and this one is entirely about codex's own store.
#
# WHERE the title lives: codex keeps it in a per-machine sqlite index,
# `~/.codex/state_<N>.sqlite`, table `threads`, column `title`, keyed by the
# thread uuid (== the rollout's uuid == the session id for a standalone host).
# The numbered filename is VERSION-FRAGILE (state_5 on the dev machine,
# 2026-07), so it is resolved by globbing and taking the highest N. It is not
# ours: we do not create it, version it, or set a pragma on it.
#
# RENAME: set_title writes threads.title — the PARKED path, the one the
# dashboard's web rename uses for a codex session with nothing running to
# overwrite it. A LIVE rename is the controller's business (codex's own
# /rename, pasted into the window), the same live-vs-parked split Claude has;
# this owns only the durable write.
import glob
import os
import re
import sqlite3

from repository.contract.titles import NativeSessionTitleRepository
from harness.models import TitleWriteOutcome
from harness.impl.codex.canonical import rollout as RO

_CODEX_DIR = os.path.join(os.path.expanduser("~"), ".codex")
_UUID = re.compile(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                   r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})")
TITLE_HEAD_LINES = 200   # rollout head lines the first-prompt fallback scans
# The index is another product's file, reached on a user-facing request path.
# Fail fast rather than hold a request open behind codex's own writer.
CONNECT_TIMEOUT_SECONDS = 2.0


def _state_database() -> str:
    """The newest codex `state_<N>.sqlite` index (highest N), or "" — resolved
    defensively because the numbered name drifts across codex versions."""
    candidates = glob.glob(os.path.join(_CODEX_DIR, "state_*.sqlite"))
    best, best_number = "", -1
    for candidate in candidates:
        match = re.search(r"state_(\d+)\.sqlite$", os.path.basename(candidate))
        number = int(match.group(1)) if match else 0
        if number >= best_number:
            best, best_number = candidate, number
    if best:
        return best
    plain = os.path.join(_CODEX_DIR, "state.sqlite")
    return plain if os.path.isfile(plain) else ""


def _thread_uuid(path: str) -> str:
    """The thread uuid a rollout path names (== the session id for a standalone
    host), or "" — read out of the `rollout-<ts>-<uuid>.jsonl` filename."""
    match = _UUID.search(os.path.basename(path or ""))
    return match.group(1) if match else ""


class CodexThreadTitleRepository(NativeSessionTitleRepository):
    def renameable(self, source_reference: str) -> bool:
        """True for a codex rollout this plugin owns — the gate both the
        dashboard's live rename and the parked write ask before naming a
        session. A standalone codex host's window carries the same session tag
        as a Claude one, so this is what keeps a Claude rename off it."""
        return bool(RO.owns(source_reference))

    def set_title(self, source_reference: str, title: str) -> TitleWriteOutcome:
        if not self.renameable(source_reference):
            return TitleWriteOutcome.UNSUPPORTED
        database = _state_database()
        thread_uuid = _thread_uuid(source_reference)
        if not database or not thread_uuid:
            return TitleWriteOutcome.UNAVAILABLE
        try:
            connection = sqlite3.connect(database, timeout=CONNECT_TIMEOUT_SECONDS)
            try:
                cursor = connection.execute(
                    "UPDATE threads SET title=? WHERE id=?", (title, thread_uuid)
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


titles = CodexThreadTitleRepository()

"""The follow list: one row per command output file being read to its end.

Rows are written by the reaction to the committed `shell.output_located` fact,
marked finishing by the reaction to `shell.finished` (foreground rows only) or
by the harness's own completion notification, and removed when the reader
reaches the end.

Nothing here touches the filesystem. `remove_expired` RETURNS what it removed
so the caller can unlink the files — deleting a user's file was previously a
side effect of listing the rows.
"""

from __future__ import annotations

from typing import Protocol

from domain.ids import SessionId, ShellId
from domain.shells import ShellOutputFollowing


class ShellOutputRepository(Protocol):
    def save(self, shell_output_following: ShellOutputFollowing) -> None:
        """Insert-or-ignore: the fact may be re-observed, the following is one."""
        ...

    def find_for_session(self, session_id: SessionId) -> tuple[ShellOutputFollowing, ...]: ...

    def mark_shell_finished(self, session_id: SessionId, shell_id: ShellId) -> None:
        """End a FOREGROUND following. A background row's launch reports
        "finished" while output keeps flowing, so it is untouched here — its end
        is `mark_finishing` or the session's."""
        ...

    def mark_finishing(self, session_id: SessionId, shell_id: ShellId) -> None:
        """The output file is complete whatever its `until`: drain and remove."""
        ...

    def outlive_shell(self, session_id: SessionId, shell_id: ShellId) -> None:
        """This following must survive its command's `finished` — the command
        moved to the background mid-run.

        Re-arms `state` as well as `until`, because the two facts come from the
        SAME raw event: if the finish is applied first the row is already
        `finishing`, and one drain later the file the job is still writing to is
        unlinked. Translators emit `shell.backgrounded` first so that does not
        happen; re-arming is what makes the order a preference rather than a
        requirement.
        """
        ...

    def remove(self, session_id: SessionId, shell_id: ShellId) -> None: ...

    def remove_expired(self, created_before: float) -> tuple[ShellOutputFollowing, ...]:
        """Drop followings older than the cutoff and return them, so the caller
        can unlink the source files it owns."""
        ...

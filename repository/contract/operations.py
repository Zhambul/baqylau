"""The follow list: one row per operation output file being read to its end.

Rows are written by the reaction to the committed `operation.output_located`
fact, marked finishing by the reaction to `operation.finished` (foreground rows
only) or by the harness's own completion notification, and removed when the
reader reaches the end.

Nothing here touches the filesystem. `remove_expired` RETURNS what it removed
so the caller can unlink the files — deleting a user's file was previously a
side effect of listing the rows.
"""

from __future__ import annotations

from typing import Protocol

from domain.ids import OperationId, SessionId
from domain.operations import OperationOutputFollowing


class OperationOutputRepository(Protocol):
    def save(self, following: OperationOutputFollowing) -> None:
        """Insert-or-ignore: the fact may be re-observed, the following is one."""
        ...

    def find_for_session(self, session_id: SessionId) -> tuple[OperationOutputFollowing, ...]: ...

    def mark_operation_finished(self, session_id: SessionId, operation_id: OperationId) -> None:
        """End a FOREGROUND following. A background row's launch reports
        "finished" while output keeps flowing, so it is untouched here — its end
        is `mark_finishing` or the session's."""
        ...

    def mark_finishing(self, session_id: SessionId, operation_id: OperationId) -> None:
        """The output file is complete whatever its `until`: drain and remove."""
        ...

    def remove(self, session_id: SessionId, operation_id: OperationId) -> None: ...

    def remove_expired(self, created_before: float) -> tuple[OperationOutputFollowing, ...]:
        """Drop followings older than the cutoff and return them, so the caller
        can unlink the source files it owns."""
        ...

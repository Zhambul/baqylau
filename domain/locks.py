"""The outcome of asking for a pid-liveness lock.

Was five magic strings, two of which carried a payload in their text
(`"claim-denied:1234"`), parsed back by prefix at the call site.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

LockDecision: TypeAlias = Literal["claimed", "stolen_stale", "denied", "unavailable"]


@dataclass(frozen=True)
class LockOutcome:
    decision: LockDecision
    holder_process_id: int | None = None

    @property
    def held(self) -> bool:
        """True when the caller now holds the lock."""
        return self.decision in ("claimed", "stolen_stale")

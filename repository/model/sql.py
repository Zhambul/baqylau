"""What SQLite can actually be handed.

The mapper builds parameter tuples for `execute` / `executemany`, and those
were typed `SqlValues` — which says "anything at all" about the one
place where the set of acceptable values is fixed and small: sqlite3 binds
exactly these five and raises InterfaceError on everything else. A Decimal, a
SessionId that was never str()-ed, a dataclass someone forgot to unpack — all
of them type-checked, and all of them failed at the driver.
"""

from __future__ import annotations

from typing import TypeAlias

# `bool` needs no mention: sqlite3 binds one as the int it is, and bool IS an
# int to the type checker.
SqlValue: TypeAlias = str | int | float | bytes | None

# One statement's parameters, in the column order the statement names.
SqlValues: TypeAlias = tuple[SqlValue, ...]

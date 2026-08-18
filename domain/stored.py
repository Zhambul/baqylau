"""The one config every stored shape carries.

`extra="forbid"` is the whole of it, and it is the check that matters: an
unknown field in a stored document is SCHEMA DRIFT — a field that was written by
a version of this tree that disagreed with this one — and it must not decode
quietly. Pydantic's default for a dataclass is to ignore what it does not
recognise, which for a wire is right and for a store is how a rename becomes
silent data loss.

Declared here and named on each dataclass it applies to, rather than passed at
every TypeAdapter: pydantic refuses `config=` on a type that could carry its
own, and a shape that is stored should say so where it is declared.
"""

from __future__ import annotations

from pydantic import ConfigDict

# `revalidate_instances="always"` is the other half, and it is what makes a WRITE
# a check. Pydantic trusts a dataclass instance it is handed — reasonably, since
# the class constructed it — but a frozen dataclass constructor does not check a
# Literal, so `replace(payload, role="tool")` builds a MessageCreated whose role
# is not one of the five. Encoding is the last moment that value is still
# attributable to whoever produced it, so encoding revalidates.
STORED = ConfigDict(extra="forbid", revalidate_instances="always")

"""The one pydantic config every stored shape shares.

Something has to REFUSE a value that does not match its declared shape, at
the write, which is the last moment the bad value is still attributable to
whoever produced it. `STORED` is the config named on every shape that is
stored — the canonical payload marker base and the value objects a payload
can nest.

`extra="forbid"` is the check that matters: an unknown field in a stored
document is SCHEMA DRIFT — a field written by a version of this tree that
disagreed with this one — and it must not decode quietly. Pydantic's default
for a dataclass is to ignore what it does not recognise, which for an HTTP
document is right and for a store is how a rename becomes silent data loss.

`revalidate_instances="always"` is the other half, and it is what makes a
WRITE a check. Pydantic trusts a dataclass instance it is handed — reasonably,
since the class constructed it — but a frozen dataclass constructor does not
check a Literal, so `replace(payload, role="tool")` builds a MessageCreated
whose role is not one of the five. Encoding is the last moment that value is
still attributable to whoever produced it, so encoding revalidates.

Declared here and named on each dataclass it applies to, rather than passed
at every `TypeAdapter`: pydantic refuses `config=` on a type that could carry
its own, and a shape that is stored should say so where it is declared.
"""

from __future__ import annotations

from pydantic import ConfigDict

STORED = ConfigDict(extra="forbid", revalidate_instances="always")

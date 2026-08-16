"""One encoding of a response: a dataclass tree becomes JSON-ready data.

Every response the daemon returns is built from frozen dataclasses — the
engine's read models, the dashboard's own snapshots — and exactly one function
turns them into what `json.dumps` accepts. Keeping it here, beside the other
renderers, is what lets the route handlers name real types in their signatures
and never hand-build a dict.

Returns DATA, not a string: tuples become lists, mappings become objects,
`Decimal` becomes a string (a cost must not round-trip through a float).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from decimal import Decimal


def json_ready(value):
    if is_dataclass(value):
        return {field.name: json_ready(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [json_ready(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    return value

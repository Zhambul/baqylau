"""Small typed parser for server-sent event envelopes."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass


@dataclass(frozen=True)
class SseEvent:
    event: str
    event_id: str | None
    data: str


def events(lines: Iterable[str]) -> Iterator[SseEvent]:
    event_name = "message"
    event_id: str | None = None
    data: list[str] = []
    seen = False
    for line in lines:
        if line == "":
            if seen:
                yield SseEvent(event_name, event_id, "\n".join(data))
            event_name = "message"
            event_id = None
            data = []
            seen = False
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if not separator:
            value = ""
        elif value.startswith(" "):
            value = value[1:]
        if field == "event":
            event_name = value
            seen = True
        elif field == "id":
            event_id = value
            seen = True
        elif field == "data":
            data.append(value)
            seen = True
    if seen:
        yield SseEvent(event_name, event_id, "\n".join(data))

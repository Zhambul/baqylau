"""The engine's own synthetic raw events, as documents.

Four raw-event payloads that nothing outside this tree ever produces: a chunk
of a followed output file, the observation that a CLI process is gone, and the
mark left by an interrupt no native raw event corroborated. They are OURS on
both ends — written by `engine/interpret/`, read by a translator — and each was
a dict literal at the writer and a field-by-field read at the reader, with
nothing but agreement between two files holding the shape together.

Declared here, beside the source-type constants that name them
(`harness/models/raw_events.py`), because both ends may import this and neither
may import the other. Encoded and decoded by `repository/mapper/documents.py`,
the one place in the tree that turns an object into bytes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from domain.ids import SessionId, ShellId
from domain.stored import STORED
from domain.values import ProgressStream, TitleOrigin


@dataclass(frozen=True)
class ShellOutputChunk:
    """One slice of a followed output file. Base64 because the bytes are a
    terminal's, and no encoding may be assumed of them until they are rendered."""

    __pydantic_config__ = STORED

    content_base64: str
    shell_id: ShellId
    ordinal: int
    stream: ProgressStream


@dataclass(frozen=True)
class ProcessExit:
    """The CLI process this session was running in is gone."""

    __pydantic_config__ = STORED

    process_id: int | None
    state: Literal["exited"]


@dataclass(frozen=True)
class InterruptMark:
    """An acknowledged interrupt whose grace period passed with nothing in the
    harness's own raw event confirming it."""

    __pydantic_config__ = STORED

    session_id: SessionId


@dataclass(frozen=True)
class NativeTitleObservation:
    """A title read from a harness store that has no native event stream."""

    __pydantic_config__ = STORED

    title: str
    origin: TitleOrigin

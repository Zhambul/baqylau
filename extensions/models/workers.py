# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep worker process limits and bounded diagnostics separate from feature documents."""

from dataclasses import dataclass
from typing import Annotated, Literal

from baqylau_extension_api.models.base import WireModel
from baqylau_extension_api.runtime.models import RequestTimeout
from pydantic import Field

type WorkerFailure = Literal["output_limit", "process_exit", "transport_closed", "load_failed"]


class WorkerPolicy(WireModel):
    """Bound startup, calls, shutdown, and total process log bytes per generation.

    Pure transforms, translation, and projection run on the engine thread, so a
    slow one delays all core input. They have their own shorter deadline: 5
    seconds is 6.5 times the slowest normal call measured (docs/extensions/performance.md).
    """

    request_seconds: RequestTimeout = 30.0
    transform_seconds: RequestTimeout = 5.0
    stop_seconds: RequestTimeout = 2.0
    output_limit: Annotated[int, Field(gt=0)] = 1_048_576

    @property
    def pure_seconds(self) -> float:
        """The deadline of one pure call: the transform deadline, and never more than the call deadline."""
        return min(self.transform_seconds, self.request_seconds)


@dataclass(frozen=True)
class WorkerDiagnostics:
    """Retain bounded bytes for later host diagnostics, without a live worker call."""

    process_id: int
    return_code: int | None
    stdout: bytes
    stderr: bytes
    failure: WorkerFailure | None = None

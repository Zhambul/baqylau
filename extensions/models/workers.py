# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep worker process limits and bounded diagnostics separate from feature documents."""

from dataclasses import dataclass
from typing import Annotated, Literal

from baqylau_extension_api.models.base import WireModel
from baqylau_extension_api.runtime.models import RequestTimeout
from pydantic import Field

type WorkerFailure = Literal["output_limit", "process_exit", "transport_closed", "load_failed"]


class WorkerPolicy(WireModel):
    """Bound startup, calls, shutdown, and total process log bytes per generation."""

    request_seconds: RequestTimeout = 30.0
    stop_seconds: RequestTimeout = 2.0
    output_limit: Annotated[int, Field(gt=0)] = 1_048_576


@dataclass(frozen=True)
class WorkerDiagnostics:
    """Retain bounded bytes for later host diagnostics, without a live worker call."""

    process_id: int
    return_code: int | None
    stdout: bytes
    stderr: bytes
    failure: WorkerFailure | None = None

# api/common/health.py — who is answering on this port.
#
# The daemon's singleton guard is the port bind, so "is it running, and which
# process is it" has to be answerable over the port itself: this is what the CLI
# reads for `status`, and whose pid it signals for `stop`. It touches no service
# — a wedged graph must not make the daemon unfindable. The boot identity is not
# here: the browser gets it on the SSE `ready` frame, which is where a client
# that must notice a restart already looks.
from __future__ import annotations

import os

from fastapi import APIRouter

from api.common.models.replies.health_response import HealthResponse

router = APIRouter()


@router.get("/api/health")
def health() -> HealthResponse:
    return HealthResponse(process_id=os.getpid())

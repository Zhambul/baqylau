# Copyright (c) 2026 Zhambyl Yermagambet
"""Report the extension work that the engine has not done yet."""

from fastapi import APIRouter

from api.diagnostics.extension_work_models import ExtensionWorkResponse
from app.provider_extension_jobs import Jobs
from app.provider_extension_passes import Passes
from app.provider_extension_registry import Registry
from extensions.work_backlog import BacklogReader

router = APIRouter(prefix="/api/diagnostics")


@router.get("/extension-work")
def extension_work(registry: Registry, passes: Passes, jobs: Jobs) -> ExtensionWorkResponse:
    """Read pending projections, pending observers, and open jobs of the active packages.

    A test signoff waits until this is empty and the raw checkpoint has no pending events.

    Returns:
        The backlog of the active packages.

    """
    reader = BacklogReader(projections=passes.projections, observers=passes.observers, jobs=jobs)
    with registry.read_snapshot() as read:
        backlog = reader.read(read.snapshot.packages)
    return ExtensionWorkResponse(
        projection_owners=backlog.projection_owners, observer_owners=backlog.observer_owners,
        open_jobs=backlog.open_jobs, empty=backlog.empty,
    )

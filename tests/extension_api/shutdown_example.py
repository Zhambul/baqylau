# Copyright (c) 2026 Zhambyl Yermagambet
"""Report shutdown uncertainty from an actual separately installed SDK worker."""

from time import sleep

from baqylau_extension_api.contracts import plugin, services
from baqylau_extension_api.models import lifecycle

from tests.extension_api.ordered_transform_example import OrderedExample

SHUTDOWN_MODE = "jobs"


class ShutdownExample(OrderedExample):
    """Keep normal transforms and inject a fault only during application shutdown."""

    def deactivate(self, request: lifecycle.DeactivationRequest) -> lifecycle.DeactivationResult:
        """Keep replacement normal so only final resource ownership is under test.

        Returns:
            An uncertain job or an invalid runtime acknowledgement.

        """
        if request.reason != "shutdown":
            return super().deactivate(request)
        if SHUTDOWN_MODE == "hang":
            sleep(60)
        if SHUTDOWN_MODE == "wrong_runtime":
            return lifecycle.DeactivationResult(runtime_revision="wrong-runtime")
        return lifecycle.DeactivationResult(
            runtime_revision=request.runtime_revision, pending_job_ids=("external-write",),
        )


def build_extension(host: services.ExtensionHostServices) -> plugin.ExtensionPlugin:
    """Build only the fixture's declared worker capabilities.

    Returns:
        The private plugin, with no imports from the host application.

    """
    return ShutdownExample(host)

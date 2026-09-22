# Copyright (c) 2026 Zhambyl Yermagambet
"""Build the public service group from the worker's validated declaration."""

from baqylau_extension_api.contracts.services import ExtensionHostServices
from baqylau_extension_api.runtime.contract import RemoteCaller
from baqylau_extension_api.runtime.proxies import RemoteDirectory
from baqylau_extension_api.runtime.service_access import RemoteServiceAccess
from baqylau_extension_api.runtime.worker_models import WorkerLoadRequest


def worker_services(request: WorkerLoadRequest, caller: RemoteCaller) -> ExtensionHostServices:
    """Expose peer service access only when the package declares consumed services.

    Returns:
        The host-selected identity and narrow callback proxies.

    """
    access = RemoteServiceAccess(caller) if request.manifest.contributions.consumes else None
    return ExtensionHostServices(RemoteDirectory(caller), request.environment, access)

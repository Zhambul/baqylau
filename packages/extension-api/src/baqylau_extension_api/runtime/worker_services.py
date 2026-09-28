# Copyright (c) 2026 Zhambyl Yermagambet
"""Build the public service group from the worker's validated declaration."""

from baqylau_extension_api.contracts.services import ExtensionHostServices
from baqylau_extension_api.runtime.contract import RemoteCaller
from baqylau_extension_api.runtime.credential_access import RemoteCredentialService
from baqylau_extension_api.runtime.process_access import RemoteInferenceService, RemoteProcessService
from baqylau_extension_api.runtime.proxies import RemoteDirectory
from baqylau_extension_api.runtime.record_access import RemoteRecordReader
from baqylau_extension_api.runtime.reporting_access import RemoteAuditService, RemoteObservationSink
from baqylau_extension_api.runtime.service_access import RemoteServiceAccess
from baqylau_extension_api.runtime.session_access import RemoteSessionDirectory
from baqylau_extension_api.runtime.worker_models import WorkerLoadRequest


def worker_services(request: WorkerLoadRequest, caller: RemoteCaller) -> ExtensionHostServices:
    """Expose peer, secret, process, inference, and record access only when the package declares them.

    Returns:
        The host-selected identity and narrow callback proxies.

    """
    access = RemoteServiceAccess(caller) if request.manifest.contributions.consumes else None
    settings = request.manifest.settings
    credentials = RemoteCredentialService(caller) if settings is not None and settings.secret_references else None
    contributions = request.manifest.contributions
    return ExtensionHostServices(
        RemoteDirectory(caller), request.environment, access, credentials,
        RemoteProcessService(caller) if contributions.processes else None,
        RemoteInferenceService(caller) if contributions.uses_inference else None,
        RemoteRecordReader(caller) if contributions.collections else None,
        RemoteObservationSink(caller) if contributions.source_types else None,
        RemoteAuditService(caller),
        RemoteSessionDirectory(caller) if contributions.uses_sessions else None,
    )

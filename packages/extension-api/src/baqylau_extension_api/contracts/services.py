# Copyright (c) 2026 Zhambyl Yermagambet
"""Expose peer metadata through a narrow host service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from baqylau_extension_api.contracts.credentials import ExtensionCredentialService
from baqylau_extension_api.contracts.processes import ExtensionInferenceService, ExtensionProcessService
from baqylau_extension_api.contracts.record_reads import ExtensionRecordReader
from baqylau_extension_api.contracts.reporting import ExtensionAuditService, ExtensionObservationSink
from baqylau_extension_api.contracts.service_access import ExtensionServiceAccess
from baqylau_extension_api.contracts.session_lists import ExtensionSessionDirectory
from baqylau_extension_api.models.environment import ExtensionEnvironment

if TYPE_CHECKING:
    from baqylau_extension_api.models.directory import DirectoryRequest, DirectorySnapshot


class ExtensionDirectory(Protocol):
    """Read installed peers without controlling their lifecycle."""

    def list_extensions(self, request: DirectoryRequest) -> DirectorySnapshot:
        """Read one consistent catalog snapshot."""


@dataclass(frozen=True)
class ExtensionHostServices:
    """Supply only the declared host services to the worker factory."""

    directory: ExtensionDirectory
    environment: ExtensionEnvironment
    service_access: ExtensionServiceAccess | None = None
    credentials: ExtensionCredentialService | None = None
    processes: ExtensionProcessService | None = None
    inference: ExtensionInferenceService | None = None
    records: ExtensionRecordReader | None = None
    observations: ExtensionObservationSink | None = None
    audit: ExtensionAuditService | None = None
    sessions: ExtensionSessionDirectory | None = None

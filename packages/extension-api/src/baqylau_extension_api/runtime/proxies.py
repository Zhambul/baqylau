# Copyright (c) 2026 Zhambyl Yermagambet
"""Implement public capabilities with typed process calls."""

from dataclasses import dataclass

from pydantic import TypeAdapter

from baqylau_extension_api.contracts.lifecycle import ExtensionLifecycle
from baqylau_extension_api.contracts.processing import ExtensionCanonicalTransformer, ExtensionRawTransformer
from baqylau_extension_api.contracts.services import ExtensionDirectory
from baqylau_extension_api.models.directory import DirectoryRequest, DirectorySnapshot
from baqylau_extension_api.models.lifecycle import (
    ActivationRequest,
    ActivationResult,
    DeactivationRequest,
    DeactivationResult,
)
from baqylau_extension_api.models.raw_transforms import RawTransformResult
from baqylau_extension_api.models.transforms import (
    CanonicalTransformRequest,
    CanonicalTransformResult,
    RawTransformRequest,
)
from baqylau_extension_api.runtime import methods
from baqylau_extension_api.runtime.contract import RemoteCaller


@dataclass(frozen=True)
class RemoteLifecycle(ExtensionLifecycle):
    """Use the same lifecycle protocol in the worker and the host."""

    caller: RemoteCaller

    def activate(self, request: ActivationRequest) -> ActivationResult:
        """Prepare a package through a revision-bound process call.

        Returns:
            The checked activation result.

        """
        return self.caller.invoke_typed(methods.ACTIVATE, request, TypeAdapter(ActivationResult))

    def deactivate(self, request: DeactivationRequest) -> DeactivationResult:
        """Release a package through a revision-bound process call.

        Returns:
            The checked deactivation result.

        """
        return self.caller.invoke_typed(methods.DEACTIVATE, request, TypeAdapter(DeactivationResult))


@dataclass(frozen=True)
class RemoteRawTransformer(ExtensionRawTransformer):
    """Transform immutable raw inputs through the public process boundary."""

    caller: RemoteCaller

    def transform(self, request: RawTransformRequest) -> RawTransformResult:
        """Call the worker without importing its feature implementation.

        Returns:
            A checked raw operation batch for host validation.

        """
        return self.caller.invoke_typed(methods.RAW_TRANSFORM, request, TypeAdapter(RawTransformResult))


@dataclass(frozen=True)
class RemoteCanonicalTransformer(ExtensionCanonicalTransformer):
    """Transform canonical candidates through the public process boundary."""

    caller: RemoteCaller

    def transform(self, canonical_request: CanonicalTransformRequest) -> CanonicalTransformResult:
        """Call the worker with the full typed canonical union.

        Returns:
            Checked operations whose ownership the host still must validate.

        """
        return self.caller.invoke_typed(
            methods.CANONICAL_TRANSFORM, canonical_request, TypeAdapter(CanonicalTransformResult),
        )


@dataclass(frozen=True)
class RemoteDirectory(ExtensionDirectory):
    """Read host peer metadata without importing other extensions."""

    caller: RemoteCaller

    def list_extensions(self, request: DirectoryRequest) -> DirectorySnapshot:
        """Use a host callback while a worker capability is running.

        Returns:
            One validated catalog snapshot.

        """
        return self.caller.invoke_typed(methods.DIRECTORY, request, TypeAdapter(DirectorySnapshot))

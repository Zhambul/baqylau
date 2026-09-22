# Copyright (c) 2026 Zhambyl Yermagambet
"""Check worker ownership and revisions before each feature callback."""

from dataclasses import dataclass

from baqylau_extension_api.contracts.plugin import ExtensionCapabilities
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.environment import ExtensionEnvironment
from baqylau_extension_api.models.events import ProcessingContext
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


@dataclass(frozen=True)
class WorkerDispatch:
    """Keep request checks separate from feature implementations and wire codecs."""

    environment: ExtensionEnvironment
    capabilities: ExtensionCapabilities

    def activate_package(self, request: ActivationRequest) -> ActivationResult:
        """Check both request and reply revisions.

        Returns:
            The package activation result.

        """
        self._check_revision(request.runtime_revision)
        response = self.capabilities.lifecycle.activate(request)
        self._check_revision(response.runtime_revision)
        return response

    def deactivate_package(self, request: DeactivationRequest) -> DeactivationResult:
        """Check the revision before releasing package resources.

        Returns:
            The package deactivation result.

        """
        self._check_revision(request.runtime_revision)
        response = self.capabilities.lifecycle.deactivate(request)
        self._check_revision(response.runtime_revision)
        return response

    def transform_raw(self, request: RawTransformRequest) -> RawTransformResult:
        """Check the pure context before using the raw handler.

        Returns:
            The package raw operations.

        Raises:
            ExtensionContractError: If the capability is absent.

        """
        self._check_context(request.context)
        transformer = self.capabilities.raw_transformer
        if transformer is None:
            message = "worker has no raw transformer"
            raise ExtensionContractError(message)
        return transformer.transform(request)

    def transform_canonical(self, request: CanonicalTransformRequest) -> CanonicalTransformResult:
        """Check the pure context before using the canonical handler.

        Returns:
            The package canonical operations.

        Raises:
            ExtensionContractError: If the capability is absent.

        """
        self._check_context(request.context)
        transformer = self.capabilities.canonical_transformer
        if transformer is None:
            message = "worker has no canonical transformer"
            raise ExtensionContractError(message)
        return transformer.transform(request)

    def _check_revision(self, revision: str) -> None:
        if revision != self.environment.runtime_revision:
            message = "capability request or result has a stale runtime revision"
            raise ExtensionContractError(message)

    def _check_context(self, context: ProcessingContext) -> None:
        self._check_revision(context.runtime_revision)
        if context.extension_id != self.environment.extension_info.extension_id:
            message = "processing context belongs to another extension"
            raise ExtensionContractError(message)

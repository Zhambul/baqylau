# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide a small external backend with no host or test helper imports."""

from dataclasses import dataclass

from baqylau_extension_api.contracts.lifecycle import ExtensionLifecycle
from baqylau_extension_api.contracts.plugin import ExtensionCapabilities, ExtensionFactory, ExtensionPlugin
from baqylau_extension_api.contracts.processing import ExtensionCanonicalTransformer, ExtensionRawTransformer
from baqylau_extension_api.contracts.services import ExtensionHostServices
from baqylau_extension_api.models.directory import DirectoryRequest
from baqylau_extension_api.models.lifecycle import (
    ActivationReady,
    ActivationRequest,
    ActivationResult,
    DeactivationRequest,
    DeactivationResult,
    ExtensionInfo,
)
from baqylau_extension_api.models.raw_transforms import RawTransformResult
from baqylau_extension_api.models.transforms import (
    CanonicalTransformRequest,
    CanonicalTransformResult,
    Drop,
    RawTransformRequest,
)


class SampleLifecycle(ExtensionLifecycle):
    """Confirm lifecycle requests for the small backend fixture."""

    def activate(self, request: ActivationRequest) -> ActivationResult:
        """Confirm readiness for the requested revision.

        Returns:
            The same runtime revision.

        """
        return ActivationReady(runtime_revision=request.runtime_revision)

    def deactivate(self, request: DeactivationRequest) -> DeactivationResult:
        """Release the fixture without leaving pending work.

        Returns:
            A complete deactivation result.

        """
        return DeactivationResult(runtime_revision=request.runtime_revision)


class SampleTransformer(ExtensionRawTransformer):
    """Suppress the input batch with an explicit reason for each input."""

    def transform(self, request: RawTransformRequest) -> RawTransformResult:
        """Build drop operations without changing the request.

        Returns:
            One suppression operation per input.

        """
        return RawTransformResult(operations=tuple(
            Drop(input_id=source.input_id, reason="sample suppression") for source in request.inputs
        ))


class SampleCanonicalTransformer(ExtensionCanonicalTransformer):
    """Suppress typed canonical candidates through the same public operation model."""

    def transform(self, canonical_request: CanonicalTransformRequest) -> CanonicalTransformResult:
        """Build explicit suppression decisions for each typed candidate.

        Returns:
            One drop operation per canonical event.

        """
        reason = "complete prior state" if canonical_request.prior_state.complete else "sample suppression"
        return CanonicalTransformResult(operations=tuple(
            Drop(input_id=fact.event_id, reason=reason) for fact in canonical_request.inputs
        ))


@dataclass(frozen=True)
class SamplePlugin(ExtensionPlugin):
    """Supply typed capabilities selected at worker construction."""

    selected: ExtensionCapabilities
    identity: ExtensionInfo

    @property
    def extension_info(self) -> ExtensionInfo:
        """The fixture's package and API identity."""
        return self.identity

    @property
    def capabilities(self) -> ExtensionCapabilities:
        """The capabilities selected for this fixture instance."""
        return self.selected


def build_extension(services: ExtensionHostServices) -> ExtensionPlugin:
    """Enable the sample transform only when its optional peer is active.

    Returns:
        A plugin that does not import its peer.

    """
    peers = services.directory.list_extensions(DirectoryRequest(active_only=True))
    has_peer = any(peer.extension_info.extension_id == "test.reader" for peer in peers.entries)
    return SamplePlugin(ExtensionCapabilities(
        lifecycle=SampleLifecycle(), raw_transformer=SampleTransformer() if has_peer else None,
        canonical_transformer=SampleCanonicalTransformer(),
    ), services.environment.extension_info)


FACTORY: ExtensionFactory = build_extension

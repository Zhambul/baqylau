# Copyright (c) 2026 Zhambyl Yermagambet
"""Select declared inputs and retain the core lifecycle facts required for cleanup."""

from baqylau_extension_api.models import canonical, content, events
from baqylau_extension_api.models.scopes import ExtensionScope

from extensions.models.interpretation_batches import LifecycleIdentity
from extensions.models.interpretation_context import ProcessingPackage

LIFECYCLE_TYPES = frozenset(("session.started", "session.finished"))


def raw_inputs(
    package: ProcessingPackage, inputs: tuple[events.RawInput, ...],
) -> tuple[events.RawInput, ...]:
    """Select only the source types declared for this raw capability.

    Returns:
        Matching inputs in their original order.

    """
    return tuple(source for source in inputs if source.source_type in _input_types(
        package, "raw_transformer", source.scope,
    ))


def canonical_inputs(
    package: ProcessingPackage, inputs: tuple[canonical.CanonicalFact, ...],
) -> tuple[canonical.CanonicalFact, ...]:
    """Select only declared canonical types without exposing unrelated input.

    Returns:
        Matching facts in their original order.

    """
    return tuple(fact for fact in inputs if _fact_type(fact) in _input_types(
        package, "canonical_transformer", fact.scope,
    ))


def selected_content(bundle: content.ContentBundle, inputs: tuple[events.RawInput, ...]) -> content.ContentBundle:
    """Keep exactly the content addressed by selected inputs.

    Returns:
        A complete bounded content set for the selected call.

    """
    needed = frozenset(source.content for source in inputs)
    blobs = tuple(blob for blob in bundle.blobs if blob.reference in needed)
    return content.ContentBundle(blobs=blobs)


def require_lifecycle_identity(
    before: tuple[canonical.CanonicalFact, ...], after: tuple[canonical.CanonicalFact, ...],
) -> None:
    """Keep start and finish identity and order while allowing valid payload changes.

    Raises:
        ValueError: If a transform drops, inserts, or changes a required lifecycle kind.

    """
    original = _lifecycle(before)
    if _lifecycle(after) != original:
        message = "canonical transform changed required core lifecycle identity or order"
        raise ValueError(message)


def _input_types(package: ProcessingPackage, capability: str, scope: ExtensionScope) -> tuple[str, ...]:
    for selection in package.manifest.contributions.processing:
        if selection.capability == capability and scope.kind in selection.scopes:
            return selection.input_types
    return ()


def _fact_type(fact: canonical.CanonicalFact) -> str:
    return fact.payload.kind if isinstance(fact, canonical.CoreFact) else fact.event_type


def _lifecycle(facts: tuple[canonical.CanonicalFact, ...]) -> tuple[LifecycleIdentity, ...]:
    core = (fact for fact in facts if isinstance(fact, canonical.CoreFact))
    identities = (LifecycleIdentity(fact.event_id, fact.payload.kind) for fact in core)
    return tuple(identity for identity in identities if identity.payload_kind in LIFECYCLE_TYPES)

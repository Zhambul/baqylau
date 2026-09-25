# Copyright (c) 2026 Zhambyl Yermagambet
"""Build complete canonical transform evidence with public SDK operations."""

from baqylau_extension_api.identities import DerivedIdentity, derived_event_id
from baqylau_extension_api.manifest.data import ProcessingSelection
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models import canonical, events, transforms
from baqylau_extension_api.processing.canonical import apply_canonical_transform
from baqylau_extension_api.schemas import SchemaSet

from domain.records import RecordedTranslationDecision
from extensions.models.interpretation_steps import AppliedStep, CanonicalTransformStep
from extensions.models.interpretations import InterpretationCommit
from tests.extension_api import operation_samples, source_samples
from tests.extension_host import interpretation_results as evidence

RAW_SELECTION = ProcessingSelection(
    capability="raw_transformer", scopes=source_samples.SCOPES,
    input_types=(operation_samples.SOURCE_TYPE, "hook"),
)
# The fixture's canonical transformer reads earlier facts, so it declares prior state.
CANONICAL_SELECTION = ProcessingSelection(
    capability="canonical_transformer", scopes=source_samples.SCOPES,
    input_types=(source_samples.EVENT_TYPE, "session.title_changed", "session.finished"), prior_state=True,
)


def manifest() -> ExtensionManifest:
    """Declare the canonical transform used by the canonical-only storage cases.

    Returns:
        The external fixture's complete data-only manifest.

    """
    return _selected_manifest((CANONICAL_SELECTION,))


def raw_manifest() -> ExtensionManifest:
    """Declare the raw transform used by raw-only storage cases.

    Returns:
        A valid source decoder and raw-transform declaration.

    """
    return _selected_manifest((RAW_SELECTION,))


def combined_manifest(*, prior_state: bool = True) -> ExtensionManifest:
    """Select both stages for complete pipeline coverage tests.

    Returns:
        One package with eligible raw and canonical processing steps.

    """
    canonical = CANONICAL_SELECTION.model_copy(update={"prior_state": prior_state})
    return _selected_manifest((RAW_SELECTION, canonical))


def _selected_manifest(processing: tuple[ProcessingSelection, ...]) -> ExtensionManifest:
    original = source_samples.manifest()
    return original.model_copy(update={
        "capabilities": (*original.capabilities, *(selected.capability for selected in processing)),
        "contributions": original.contributions.model_copy(update={"processing": processing}),
    })


def request(original: InterpretationCommit) -> transforms.CanonicalTransformRequest:
    """Use the captured context and current candidate facts.

    Returns:
        A complete canonical request at the original accepted boundary.

    """
    return transforms.CanonicalTransformRequest(
        context=evidence.translation(original).request.context, inputs=original.proposal.facts,
        prior_state=canonical.CoreStateSnapshot(after_cursor=original.proposal.binding.expected_canonical_cursor),
    )


def apply(original: InterpretationCommit, response: transforms.CanonicalTransformResult) -> InterpretationCommit:
    """Use the SDK to construct a complete trace for the repository to check again.

    Returns:
        Original translation evidence followed by one complete transform.

    """
    selected = request(original)
    output = apply_canonical_transform(selected, response, SchemaSet(manifest().schemas))
    step = CanonicalTransformStep(request=selected, outcome=AppliedStep(reply=response))
    decision = RecordedTranslationDecision.TRANSLATED if output else RecordedTranslationDecision.SUPPRESSED
    return original.model_copy(update={"proposal": original.proposal.model_copy(update={
        "facts": output, "steps": (*original.proposal.steps, step), "decision": decision,
        "reason": None if output else "All candidates were dropped",
    })})


def insertion(fact: canonical.CanonicalFact) -> transforms.Insert[canonical.CanonicalFact]:
    """Anchor a valid owned fact to a prior candidate.

    Returns:
        A stable addition which retains its cause even if the anchor is dropped.

    """
    identity = DerivedIdentity(extension_id=operation_samples.OWNER, input_id=fact.event_id, output_key="extra")
    return transforms.Insert(
        input_id=fact.event_id, output_key=identity.output_key, position="after",
        document=events.ExtensionFact(
            event_id=derived_event_id(identity), scope=fact.scope, event_type=source_samples.EVENT_TYPE,
            document=operation_samples.query_request().arguments, causes=(fact.event_id,),
        ),
    )

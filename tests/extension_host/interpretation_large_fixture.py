# Copyright (c) 2026 Zhambyl Yermagambet
"""Build large but valid canonical additions for journal size tests."""

from baqylau_extension_api.identities import DerivedIdentity, derived_event_id
from baqylau_extension_api.models import canonical, documents, events, scopes, transforms

from extensions.models import interpretation_steps as steps
from tests.extension_api import operation_samples, source_samples

LARGE_TEXT = "x" * (documents.MAX_DOCUMENT_CHARACTERS - 2)
LARGE_DOCUMENT = f'"{LARGE_TEXT}"'
INSERTION_COUNT = 20
FACT_COUNT = INSERTION_COUNT + 1


def canonical_request() -> transforms.CanonicalTransformRequest:
    """Build one minimal canonical request for a hand-checked step.

    Returns:
        The complete request with no selected inputs.

    """
    return transforms.CanonicalTransformRequest(
        context=events.ProcessingContext(
            extension_id=operation_samples.OWNER, runtime_revision="runtime-1", history_revision="default",
            scope=scopes.InstallationScope(), input_cursor=1, settings_revision=0,
        ),
        inputs=(),
        prior_state=canonical.CoreStateSnapshot(after_cursor=0),
    )


def large_insertions(
    anchor: canonical.CanonicalFact, count: int, encoded: str,
) -> tuple[transforms.Insert[canonical.CanonicalFact], ...]:
    """Anchor several valid owned facts with one exact document text each.

    Returns:
        Stable additions with distinct output keys.

    """
    document = operation_samples.query_request(encoded=encoded).arguments
    return tuple(
        transforms.Insert(
            input_id=anchor.event_id, output_key=f"item-{index}", position="after",
            document=events.ExtensionFact(
                event_id=derived_event_id(DerivedIdentity(
                    extension_id=operation_samples.OWNER, input_id=anchor.event_id, output_key=f"item-{index}",
                )),
                scope=anchor.scope, event_type=source_samples.EVENT_TYPE, document=document,
                causes=(anchor.event_id,),
            ),
        )
        for index in range(count)
    )


def large_applied_step() -> steps.CanonicalTransformStep:
    """Build one applied canonical step with a large referenced fact body.

    Returns:
        The complete applied step.

    """
    identity = DerivedIdentity(extension_id=operation_samples.OWNER, input_id="anchor-one", output_key="extra")
    fact = events.ExtensionFact(
        event_id=derived_event_id(identity), scope=scopes.InstallationScope(), event_type=source_samples.EVENT_TYPE,
        document=operation_samples.query_request(encoded=LARGE_DOCUMENT).arguments, causes=("anchor-one",),
    )
    operation: transforms.Insert[canonical.CanonicalFact] = transforms.Insert(
        input_id="anchor-one", output_key="extra", position="after", document=fact,
    )
    return steps.CanonicalTransformStep(
        request=canonical_request(),
        outcome=steps.AppliedStep(reply=transforms.CanonicalTransformResult(operations=(operation,))),
    )

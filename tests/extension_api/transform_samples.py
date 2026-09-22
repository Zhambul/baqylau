# Copyright (c) 2026 Zhambyl Yermagambet
"""Build canonical transform requests and stable additions."""

from baqylau_extension_api.core.sessions import SessionTitleChanged
from baqylau_extension_api.identities import DerivedIdentity, derived_event_id
from baqylau_extension_api.models.canonical import CanonicalFact, CoreFact, CoreStateSnapshot
from baqylau_extension_api.models.events import ExtensionFact
from baqylau_extension_api.models.transforms import CanonicalTransformRequest, Insert

from tests.extension_api import samples


def core_fact(event_id: str = "core-1") -> CoreFact:
    """Return a simple complete canonical candidate.

    Returns:
        A session title fact with recorded source links.

    """
    return CoreFact(
        event_id=event_id, scope=samples.SESSION,
        payload=SessionTitleChanged(title="Original", origin="custom"), raw_event_ids=("raw-1",),
    )


def canonical_request(*facts: CanonicalFact) -> CanonicalTransformRequest:
    """Pin a transform request to the shared fixture context.

    Returns:
        A batch with an empty prior boundary.

    """
    return CanonicalTransformRequest(
        context=samples.processing_context(), inputs=facts, prior_state=CoreStateSnapshot(after_cursor=0),
    )


def extension_addition(original: CanonicalFact, key: str = "extra") -> Insert[CanonicalFact]:
    """Allocate one extension addition from its input and output key.

    Returns:
        A valid anchored addition with a declared text schema.

    """
    identity = DerivedIdentity(extension_id=samples.EXTENSION_ID, input_id=original.event_id, output_key=key)
    return Insert(
        input_id=original.event_id, output_key=key, position="after",
        document=ExtensionFact(
            event_id=derived_event_id(identity), scope=original.scope, event_type=f"{samples.EXTENSION_ID}.created",
            document=samples.encoded_document(), causes=(original.event_id,),
        ),
    )

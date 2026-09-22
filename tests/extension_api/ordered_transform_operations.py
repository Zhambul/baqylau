# Copyright (c) 2026 Zhambyl Yermagambet
"""Supply SDK-only operations for separate ordered worker packages."""

from baqylau_extension_api import identities
from baqylau_extension_api.models import canonical, content, documents, events, transforms
from pydantic import TypeAdapter

FIRST_OWNER = "test.z-first"
SECOND_OWNER = "test.a-second"
RAW_KEY = "raw-extra"
FACT_KEY = "fact-extra"
TEXT = TypeAdapter(str)


def decode_text(encoded: str) -> str:
    """Read a declared string document.

    Returns:
        The complete fixture text.

    """
    return TEXT.validate_json(encoded)


def append_text(encoded: str, suffix: str) -> str:
    """Change content without changing its schema.

    Returns:
        A complete encoded string document.

    """
    return TEXT.dump_json(f"{decode_text(encoded)}/{suffix}").decode("utf-8")


def raw_content(request: transforms.RawTransformRequest, source: events.RawInput) -> str:
    """Resolve recorded bytes without reading the source file.

    Returns:
        The original encoded string.

    """
    blob = request.content_snapshot.resolve(source.content)
    return content.decode_base64(blob.base64_text).decode("utf-8")


def raw_addition(source: events.RawInput) -> transforms.Insert[events.RawInput]:
    """Add one stable translation input after its anchor.

    Returns:
        A derived input which uses the same captured bytes.

    """
    identity = identities.DerivedIdentity(extension_id=FIRST_OWNER, input_id=source.input_id, output_key=RAW_KEY)
    return transforms.Insert(
        input_id=source.input_id, output_key=RAW_KEY, position="after",
        document=source.model_copy(update={"input_id": identities.derived_input_id(identity)}),
    )


def fact_addition(fact: canonical.CanonicalFact, owner: str) -> transforms.Insert[canonical.CanonicalFact]:
    """Add an owned fact with its exact anchor as a cause.

    Returns:
        One stable fact which later workers can select.

    """
    assert fact.kind == "extension"
    identity = identities.DerivedIdentity(extension_id=owner, input_id=fact.event_id, output_key=FACT_KEY)
    reference = fact.document.schema_ref.model_copy(update={"owner": owner})
    return transforms.Insert(
        input_id=fact.event_id, output_key=FACT_KEY, position="after",
        document=events.ExtensionFact(
            event_id=identities.derived_event_id(identity), scope=fact.scope, event_type=f"{owner}.fact",
            document=documents.EncodedDocument(schema_ref=reference, json_text='"added"'), causes=(fact.event_id,),
        ),
    )

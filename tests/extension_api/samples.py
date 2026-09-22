# Copyright (c) 2026 Zhambyl Yermagambet
"""Build small extension documents for public contract tests."""

import hashlib

from baqylau_extension_api.models.content import encode_content
from baqylau_extension_api.models.documents import EncodedDocument, SchemaDefinition, SchemaRef
from baqylau_extension_api.models.environment import ExtensionEnvironment
from baqylau_extension_api.models.events import ProcessingContext, RawInput, SourceReference
from baqylau_extension_api.models.lifecycle import ExtensionInfo
from baqylau_extension_api.models.scopes import SessionScope
from baqylau_extension_api.versions import API_VERSION

EXTENSION_ID = "test.reader"
DIGEST_LENGTH = 64
DIGEST = "a" * DIGEST_LENGTH
RUNTIME_REVISION = "runtime-1"
TEXT_SCHEMA = '{"type":"string"}'
SESSION = SessionScope(kind="session", session_id="session-1", actor_id="actor-1", harness="test")


def schema_definition(encoded: str = TEXT_SCHEMA, name: str = "text") -> SchemaDefinition:
    """Return a content-addressed schema fixture.

    Returns:
        A schema with the exact digest of its source bytes.

    """
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    return SchemaDefinition(
        reference=SchemaRef(
            owner=EXTENSION_ID, name=name, version=1, digest=digest,
        ),
        json_text=encoded,
    )


def encoded_document(encoded: str = '"hello"') -> EncodedDocument:
    """Return one document with the default text schema.

    Returns:
        An encoded text document.

    """
    return EncodedDocument(schema_ref=schema_definition().reference, json_text=encoded)


def extension_info() -> ExtensionInfo:
    """Return a backend identity fixture.

    Returns:
        One package identity using the current draft API.

    """
    return ExtensionInfo(
        extension_id=EXTENSION_ID, package_version="1.0.0", api_version=API_VERSION, package_digest=DIGEST,
    )


def raw_input(input_id: str = "raw-1") -> RawInput:
    """Return a recorded raw input fixture.

    Returns:
        One session input with immutable source and content references.

    """
    return RawInput(
        input_id=input_id,
        scope=SESSION,
        source_type="test.record",
        source=SourceReference(raw_event_id=input_id, source_identity="source-1", source_position="1"),
        content=encode_content(b"hello", "text/plain").reference,
        origin="harness",
        owner="test",
    )


def processing_context() -> ProcessingContext:
    """Return a pure processing context fixture.

    Returns:
        A context with no live host service handles.

    """
    return ProcessingContext(
        extension_id=EXTENSION_ID,
        runtime_revision=RUNTIME_REVISION,
        history_revision="history-1",
        scope=SESSION,
        input_cursor=1,
        settings_revision=0,
    )


def worker_environment() -> ExtensionEnvironment:
    """Return the host-verified identity for the SDK-only sample package.

    Returns:
        An environment distinct from the optional peer identity.

    """
    return ExtensionEnvironment(
        extension_info=extension_info().model_copy(update={"extension_id": "test.sample"}),
        runtime_revision=RUNTIME_REVISION,
    )

# Copyright (c) 2026 Zhambyl Yermagambet
"""Check strict extension wire models before worker transport exists."""

import pytest
from baqylau_extension_api.models.documents import MAX_DOCUMENT_CHARACTERS, EncodedDocument
from baqylau_extension_api.models.lifecycle import ActivationRequest, ExtensionInfo
from baqylau_extension_api.models.scopes import ExtensionScope
from pydantic import TypeAdapter, ValidationError

from tests.extension_api import samples as fixtures

SCOPE_ADAPTER: TypeAdapter[ExtensionScope] = TypeAdapter(ExtensionScope)


@pytest.mark.parametrize("encoded", [
    '{"kind":"session","session_id":"s","actor_id":"a","harness":"test"}',
    '{"kind":"workspace","workspace_id":"w"}',
    '{"kind":"repository","repository_id":"r","worktree":"/work","git_directory":"/work/.git"}',
    '{"kind":"installation"}',
])
def test_scope_round_trip(encoded: str) -> None:
    """Keep each explicit scope when the wire document is encoded again."""
    scope = SCOPE_ADAPTER.validate_json(encoded, strict=True)
    assert SCOPE_ADAPTER.validate_json(SCOPE_ADAPTER.dump_json(scope), strict=True) == scope


@pytest.mark.parametrize("encoded", [
    '{"kind":"session","session_id":"s","harness":"test"}',
    '{"kind":"session","session_id":"s","actor_id":"","harness":"test"}',
    '{"kind":"workspace"}',
    '{"kind":"repository","repository_id":"r","worktree":"/work"}',
    '{"kind":"installation","session_id":"s"}',
    '{"kind":"unknown"}',
])
def test_scope_rejects_missing_or_extra_context(encoded: str) -> None:
    """Reject incomplete scopes and fields from a different scope."""
    with pytest.raises(ValidationError):
        SCOPE_ADAPTER.validate_json(encoded, strict=True)


@pytest.mark.parametrize("encoded", [
    '{"runtime_revision":"r","settings_revision":"1"}',
    '{"runtime_revision":"r","settings_revision":true}',
    '{"runtime_revision":"r","settings_revision":-1}',
    '{"runtime_revision":"r","settings_revision":0,"connection":"db"}',
])
def test_wire_rejects_coercion_and_extra(encoded: str) -> None:
    """Do not turn untrusted process messages into plausible model data."""
    with pytest.raises(ValidationError):
        ActivationRequest.model_validate_json(encoded)


def test_wire_models_reject_assignment() -> None:
    """Prevent a worker from changing the supplied request in place."""
    request = ActivationRequest(runtime_revision="r", settings_revision=0)
    with pytest.raises(ValidationError, match="frozen"):
        request.settings_revision = 1


def test_document_limits_inline_content() -> None:
    """Require content references for documents beyond the inline limit."""
    with pytest.raises(ValidationError, match="string_too_long"):
        EncodedDocument(
            schema_ref=fixtures.schema_definition().reference, json_text="x" * (MAX_DOCUMENT_CHARACTERS + 1),
        )


def test_identity_rejects_invalid_version() -> None:
    """Reject package versions that packaging cannot compare."""
    with pytest.raises(ValidationError, match="Invalid version"):
        ExtensionInfo(
            extension_id=fixtures.EXTENSION_ID, package_version="not a version",
            api_version="0.1.0a1", package_digest=fixtures.DIGEST,
        )

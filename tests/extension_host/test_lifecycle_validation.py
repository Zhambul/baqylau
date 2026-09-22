# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject invalid lifecycle input before any request or runtime reservation is stored."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from extensions.models import lifecycle_operations
from tests.extension_host import lifecycle_fixture as fixtures

DIGEST_HEX_LENGTH = 64


@pytest.mark.parametrize("change", ["unknown_digest", "owner", "intent"])
def test_invalid_selection_is_not_reserved(tmp_path: Path, change: str) -> None:
    """Package identity and requested state must agree with the retained manifest."""
    store = fixtures.claimed_repository(tmp_path)
    package = fixtures.install_package(tmp_path)
    proposed = fixtures.proposal(store, package)
    if change == "unknown_digest":
        identity = package.extension_info.model_copy(update={"package_digest": "f" * DIGEST_HEX_LENGTH})
        proposed = fixtures.proposal(store, package.model_copy(update={"extension_info": identity}))
    if change == "owner":
        identity = package.extension_info.model_copy(update={"extension_id": "wrong.owner"})
        proposed = fixtures.proposal(store, package.model_copy(update={"extension_info": identity}))
    if change == "intent":
        intent = proposed.intents[0].model_copy(update={"enabled": False})
        proposed = proposed.model_copy(update={"intents": (intent,)})
    with pytest.raises(ValueError, match=r"manifest|order|identity|state"):
        store.accept_extension_operation(proposed, fixtures.NOW)
    assert store.read_extension_operation(proposed.operation_id) is None
    assert store.read_extension_runtime(proposed.candidate.runtime_revision) is None


@pytest.mark.parametrize("timestamp", [-1.0, float("nan"), float("inf")])
def test_bad_creation_time_is_rejected(tmp_path: Path, timestamp: float) -> None:
    """Invalid wire times cannot reserve a candidate or change the stored head."""
    store = fixtures.claimed_repository(tmp_path)
    proposed = fixtures.proposal(store)
    with pytest.raises(ValidationError):
        store.accept_extension_operation(proposed, timestamp)
    assert store.read_extension_lifecycle().revision == proposed.expected_revision


def test_operation_size_checked_before_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Revalidate an existing model when admission enforces its encoded byte limit."""
    store = fixtures.claimed_repository(tmp_path)
    proposed = fixtures.proposal(store)
    monkeypatch.setattr(lifecycle_operations, "MAX_OPERATION_BYTES", 1)
    with pytest.raises(ValidationError, match="document limit"):
        store.accept_extension_operation(proposed, fixtures.NOW)
    assert store.read_extension_lifecycle().pending_operation is None

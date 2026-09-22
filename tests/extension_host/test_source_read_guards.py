# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject stale source authority and changed captured inputs before any write."""

from pathlib import Path

import pytest

from tests.extension_api import operation_samples, source_samples
from tests.extension_host import observation_requests as originals, source_read_fixture as fixtures
from tests.extension_host.source_read_assertions import require_rejected


def test_wrong_manager_rejects(tmp_path: Path) -> None:
    """A source cannot commit under a manager that does not own the database."""
    case = fixtures.installed(tmp_path)
    request = fixtures.proposal(case)
    request = request.model_copy(update={"proposal": request.proposal.model_copy(
        update={"manager_id": "wrong"},
    )})
    require_rejected(case, request, "committed runtime")


@pytest.mark.parametrize("already_committed", [False, True])
def test_old_runtime_cannot_commit(tmp_path: Path, *, already_committed: bool) -> None:
    """A runtime change rejects both a late first reply and an exact old retry."""
    case = fixtures.installed(tmp_path)
    request = fixtures.proposal(case)
    if already_committed:
        case.store.record_source_read(request)
    originals.reload_request(case.original)
    require_rejected(case, request, "committed runtime")


@pytest.mark.parametrize("field", ["settings_revision", "settings"])
def test_changed_settings_reject(tmp_path: Path, field: str) -> None:
    """A schema-valid document is not authority to use different effective settings."""
    defaults = operation_samples.query_request().arguments
    case = fixtures.installed(tmp_path, source_samples.manifest(defaults))
    request = fixtures.proposal(case)
    context = request.proposal.request.context.model_copy(update={
        field: 1 if field == "settings_revision" else defaults.model_copy(update={"json_text": '"changed"'}),
    })
    request = fixtures.with_request(request, request.proposal.request.model_copy(
        update={"context": context},
    ))
    require_rejected(case, request, "source settings")


def test_changed_retry_rejects(tmp_path: Path) -> None:
    """A call cannot replace its accepted reply, including its future read deadline."""
    case = fixtures.installed(tmp_path)
    request = fixtures.proposal(case)
    case.store.record_source_read(request)
    response = request.proposal.response.model_copy(update={"next_due_at": 3000.0})
    request = request.model_copy(update={"proposal": request.proposal.model_copy(
        update={"response": response},
    )})
    require_rejected(case, request, "call ID reused")


def test_stale_checkpoint_rejects(tmp_path: Path) -> None:
    """Two reads from one captured checkpoint cannot both advance the source."""
    case = fixtures.installed(tmp_path)
    stale = fixtures.proposal(case, "read-2", "position-2")
    case.store.record_source_read(fixtures.proposal(case))
    require_rejected(case, stale, "checkpoint is stale")


def test_repeated_position_cannot_hide_stale_read(tmp_path: Path) -> None:
    """The host revision rejects an old capture even when opaque positions cycle back."""
    case = fixtures.installed(tmp_path)
    case.store.record_source_read(fixtures.proposal(case))
    stale = fixtures.proposal(case, "stale", "position-3", emit=False)
    case.store.record_source_read(fixtures.proposal(case, "read-2", "position-2", emit=False))
    case.store.record_source_read(fixtures.proposal(case, "read-3", "position-1", emit=False))
    require_rejected(case, stale, "checkpoint is stale")

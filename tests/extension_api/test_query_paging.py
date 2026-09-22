# Copyright (c) 2026 Zhambyl Yermagambet
"""Bind query continuation to its operation, arguments, scope, and snapshot."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.operations import QueryPageCursor, QuerySnapshot
from baqylau_extension_api.models.queries import QueryReady
from baqylau_extension_api.operations import paging

from tests.extension_api import operation_samples, samples, terminal_samples

PAGE_FIELD = "page"
SNAPSHOT_FIELD = "snapshot"


def page_cursor() -> QueryPageCursor:
    """Select the second page of the fixed fixture query.

    Returns:
        A cursor bound to the default query and a stored snapshot.

    """
    return QueryPageCursor(
        selection=paging.query_selection(operation_samples.query_request()), position="page-2",
        snapshot=QuerySnapshot(state_revision="state-1", cursor=terminal_samples.view_request().binding.snapshot),
    )


def test_page_keeps_snapshot_across_new_call_ids() -> None:
    """Permit a new request ID and smaller page size for the same selection."""
    request = operation_samples.query_request()
    request = request.model_copy(update={
        PAGE_FIELD: page_cursor(), "binding": request.binding.model_copy(update={"call_id": "next-call"}), "limit": 1,
    })
    paging.validate_query_page(request)
    assert paging.selected_snapshot(request) == page_cursor().snapshot


@pytest.mark.parametrize("change", [
    {"extension_id": "peer"}, {"operation_id": "test.sample.other"},
    {"runtime_revision": "stale"}, {"scope": samples.SESSION},
])
def test_page_rejects_a_changed_binding(change: dict[str, object]) -> None:
    """Reject a page token from another operation context."""
    request = operation_samples.query_request()
    changed_binding = request.binding.model_copy(update=change)
    request = request.model_copy(update={PAGE_FIELD: page_cursor(), "binding": changed_binding})
    with pytest.raises(ExtensionContractError, match="selected operation inputs"):
        paging.validate_query_page(request)


@pytest.mark.parametrize("change", [
    {"settings_revision": 1}, {"arguments": operation_samples.query_request('"changed"').arguments},
])
def test_page_rejects_changed_read_inputs(change: dict[str, object]) -> None:
    """Do not reuse a cursor after filters or effective settings change."""
    request = operation_samples.query_request().model_copy(update={PAGE_FIELD: page_cursor(), **change})
    with pytest.raises(ExtensionContractError, match="selected operation inputs"):
        paging.validate_query_page(request)


def test_page_requires_one_consistent_snapshot() -> None:
    """Reject an explicit snapshot that differs from the supplied cursor."""
    request = operation_samples.query_request().model_copy(update={
        PAGE_FIELD: page_cursor(), SNAPSHOT_FIELD: QuerySnapshot(state_revision="other-state"),
    })
    with pytest.raises(ExtensionContractError, match="snapshot do not match"):
        paging.validate_query_page(request)


def test_query_snapshot_checks_stored_scope() -> None:
    """Reject stored cursors whose scope differs from the query selection."""
    cursor = terminal_samples.view_request().binding.snapshot.model_copy(update={"scope": samples.SESSION})
    request = operation_samples.query_request().model_copy(update={
        SNAPSHOT_FIELD: QuerySnapshot(state_revision="state-1", cursor=cursor),
    })
    with pytest.raises(ExtensionContractError, match="another scope"):
        paging.validate_query_page(request)


@pytest.mark.parametrize("change", [
    {SNAPSHOT_FIELD: QuerySnapshot(state_revision="other-state")},
    {"next_page": page_cursor()},
    {"next_page": page_cursor().model_copy(update={SNAPSHOT_FIELD: QuerySnapshot(state_revision="other-state")})},
])
def test_query_continuation_cannot_change_state(change: dict[str, object]) -> None:
    """Reject changed result state, repeated page tokens, and mixed snapshots."""
    request = operation_samples.query_request().model_copy(update={PAGE_FIELD: page_cursor()})
    response = QueryReady(binding=request.binding, document=request.arguments, snapshot=page_cursor().snapshot)
    with pytest.raises(ExtensionContractError):
        paging.validate_query_continuation(request, response.model_copy(update=change))

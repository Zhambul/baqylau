# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep query continuation tokens tied to the same immutable selection."""

import hashlib

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.operations import QueryPageCursor, QuerySelection, QuerySnapshot
from baqylau_extension_api.models.queries import QueryReady, QueryRequest


def query_selection(request: QueryRequest) -> QuerySelection:
    """Bind continuation to the complete encoded arguments and settings revision.

    Returns:
        A selection that excludes the per-call ID and page size.

    """
    binding = request.binding
    digest = hashlib.sha256(request.arguments.model_dump_json().encode("utf-8")).hexdigest()
    return QuerySelection(
        extension_id=binding.extension_id, operation_id=binding.operation_id, scope=binding.scope,
        runtime_revision=binding.runtime_revision, settings_revision=request.settings_revision, arguments_digest=digest,
    )


def selected_snapshot(request: QueryRequest) -> QuerySnapshot | None:
    """Read the fixed state selected by a page cursor or explicit snapshot.

    Returns:
        The requested state, or None when the query must select a new state.

    """
    return request.snapshot if request.page is None else request.page.snapshot


def validate_query_page(request: QueryRequest) -> None:
    """Reject a page from a different query, scope, runtime, settings, or argument set.

    Raises:
        ExtensionContractError: If an explicit snapshot conflicts with a page.

    """
    if request.page is not None:
        _require_selection(request, request.page)
        if request.snapshot is not None and request.snapshot != request.page.snapshot:
            message = "query page and requested snapshot do not match"
            raise ExtensionContractError(message)
    snapshot = selected_snapshot(request)
    if snapshot is not None:
        _require_snapshot_scope(request, snapshot)


def validate_query_continuation(request: QueryRequest, response: QueryReady) -> None:
    """Reject a page result that changes a selected snapshot or its continuation.

    Raises:
        ExtensionContractError: If a reply cannot continue the requested read.

    """
    _require_snapshot_scope(request, response.snapshot)
    expected = selected_snapshot(request)
    if expected is not None and response.snapshot != expected:
        message = "query result changed the selected snapshot"
        raise ExtensionContractError(message)
    if response.next_page is not None:
        _require_selection(request, response.next_page)
        if response.next_page.snapshot != response.snapshot:
            message = "query continuation changed its result snapshot"
            raise ExtensionContractError(message)
        if response.next_page == request.page:
            message = "query continuation did not advance its page token"
            raise ExtensionContractError(message)


def _require_selection(request: QueryRequest, cursor: QueryPageCursor) -> None:
    if cursor.selection != query_selection(request):
        message = "query cursor does not match its selected operation inputs"
        raise ExtensionContractError(message)


def _require_snapshot_scope(request: QueryRequest, snapshot: QuerySnapshot) -> None:
    if snapshot.cursor is not None and snapshot.cursor.scope != request.binding.scope:
        message = "query snapshot belongs to another scope"
        raise ExtensionContractError(message)

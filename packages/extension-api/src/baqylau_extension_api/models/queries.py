# Copyright (c) 2026 Zhambyl Yermagambet
"""Describe bounded reads of stored or live extension data."""

from typing import Annotated, Literal

from pydantic import Field

from baqylau_extension_api.models.base import Revision, WireModel
from baqylau_extension_api.models.documents import ContentReference, Diagnostic, EncodedDocument
from baqylau_extension_api.models.operations import OperationBinding, QueryPageCursor, QuerySnapshot

MAX_QUERY_LIMIT = 200
DEFAULT_QUERY_LIMIT = 100
MAX_RESULT_CONTENT = 128


class QueryRequest(WireModel):
    """Select a bounded read without accepting an external write."""

    binding: OperationBinding
    arguments: EncodedDocument
    settings_revision: Revision
    settings: EncodedDocument | None = None
    snapshot: QuerySnapshot | None = None
    page: QueryPageCursor | None = None
    limit: Annotated[int, Field(ge=1, le=MAX_QUERY_LIMIT)] = DEFAULT_QUERY_LIMIT


class QueryReady(WireModel):
    """Return a schema-valid page and the exact state from which it was read."""

    status: Literal["ready"] = "ready"
    binding: OperationBinding
    document: EncodedDocument
    snapshot: QuerySnapshot
    next_page: QueryPageCursor | None = None
    content: Annotated[tuple[ContentReference, ...], Field(max_length=MAX_RESULT_CONTENT)] = ()


class QueryFailed(WireModel):
    """Report an unavailable or invalid read without claiming an empty result."""

    status: Literal["failed"] = "failed"
    binding: OperationBinding
    diagnostic: Diagnostic


QueryResult = Annotated[QueryReady | QueryFailed, Field(discriminator="status")]

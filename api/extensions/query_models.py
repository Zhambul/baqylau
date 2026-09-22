# Copyright (c) 2026 Zhambyl Yermagambet
"""Typed declared query requests and responses."""

from typing import Annotated

from baqylau_extension_api.models import queries
from baqylau_extension_api.models.operations import QueryPageCursor
from pydantic import BaseModel, Field


class ExtensionQueryRequest(BaseModel):
    """Select one declared read with its exact scope, arguments, and page."""

    scope: str
    arguments: str
    page: QueryPageCursor | None = None
    limit: Annotated[int, Field(ge=1, le=queries.MAX_QUERY_LIMIT)] = queries.DEFAULT_QUERY_LIMIT


class ExtensionQueryReadyResponse(queries.QueryReady):
    """Publish one ready query result through the api layer."""


class ExtensionQueryFailedResponse(queries.QueryFailed):
    """Publish one failed query result through the api layer."""


type ExtensionQueryResult = Annotated[
    ExtensionQueryReadyResponse | ExtensionQueryFailedResponse,
    Field(discriminator="status"),
]


def query_response(result: queries.QueryResult) -> ExtensionQueryResult:
    """Map one checked SDK query result to its api response.

    Returns:
        The typed api response.

    """
    if isinstance(result, queries.QueryReady):
        return ExtensionQueryReadyResponse(
            binding=result.binding,
            document=result.document,
            snapshot=result.snapshot,
            next_page=result.next_page,
            content=result.content,
        )
    return ExtensionQueryFailedResponse(binding=result.binding, diagnostic=result.diagnostic)

# Copyright (c) 2026 Zhambyl Yermagambet
"""Run declared extension reads through the public API."""

from dataclasses import dataclass
from http import HTTPStatus

from pydantic import TypeAdapter

from api.extensions.query_models import ExtensionQueryRequest, ExtensionQueryResult
from sdk.transport import HttpTransport

QUERY_RESULT: TypeAdapter[ExtensionQueryResult] = TypeAdapter(ExtensionQueryResult)


@dataclass(frozen=True)
class ExtensionQueriesResource:
    """Keep declared reads separate from catalog and lifecycle calls."""

    transport: HttpTransport

    def query(
        self,
        extension_id: str,
        query_id: str,
        request: ExtensionQueryRequest,
    ) -> ExtensionQueryResult:
        """Run one declared extension read.

        Returns:
            The typed query result.

        """
        _, response = self.transport.post(
            f"/api/extensions/{extension_id}/queries/{query_id}",
            request,
            QUERY_RESULT,
            {HTTPStatus.OK},
        )
        return response

# Copyright (c) 2026 Zhambyl Yermagambet
"""Call declared extension reads through typed process adapters."""

from dataclasses import dataclass

from pydantic import TypeAdapter

from baqylau_extension_api.contracts.operations import ExtensionQueries
from baqylau_extension_api.models.queries import QueryRequest, QueryResult
from baqylau_extension_api.operations import queries, registration
from baqylau_extension_api.runtime import methods
from baqylau_extension_api.runtime.channel import RpcChannel
from baqylau_extension_api.runtime.codec import ModelHandler
from baqylau_extension_api.runtime.contract import RemoteCaller
from baqylau_extension_api.runtime.worker_models import WorkerLoadRequest
from baqylau_extension_api.schemas import SchemaSet


@dataclass(frozen=True)
class RemoteQueries(ExtensionQueries):
    """Read through the public protocol without importing feature modules."""

    caller: RemoteCaller

    def query(self, query_request: QueryRequest) -> QueryResult:
        """Require an exact call binding and checked continuation on each reply.

        Returns:
            The bounded query success or typed failure.

        """
        response = self.caller.invoke_typed(methods.QUERY, query_request, TypeAdapter[QueryResult](QueryResult))
        return queries.validate_query_response(query_request, response)


@dataclass(frozen=True)
class WorkerQueries(ExtensionQueries):
    """Guard schemas, runtime identity, and page cursors around feature reads."""

    provider: ExtensionQueries
    load: WorkerLoadRequest
    schemas: SchemaSet

    def query(self, query_request: QueryRequest) -> QueryResult:
        """Validate complete input and output while keeping the reader active.

        Returns:
            One checked feature read without an external write.

        """
        request = queries.validate_query_request(self.load.manifest, self.schemas, query_request)
        registration.require_operation_environment(request.binding, self.load.environment)
        response = queries.validate_query_response(request, self.provider.query(request))
        queries.validate_query_document(self.load.manifest, self.schemas, response)
        return response


def register_queries(
    channel: RpcChannel, provider: ExtensionQueries, request: WorkerLoadRequest, schemas: SchemaSet,
) -> None:
    """Keep a slow read outside the pure transform lane."""
    bound = WorkerQueries(provider, request, schemas)
    channel.register(methods.QUERY, ModelHandler(
        QueryRequest, TypeAdapter(QueryResult), bound.query,
    ), "live")

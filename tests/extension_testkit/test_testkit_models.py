# Copyright (c) 2026 Zhambyl Yermagambet
"""Every field that the kit reads is in the host's published reply schema (P08-T01).

The kit has its own reply models, so an extension test needs no host module. This
test finds a host change that removes or renames a field that the kit reads.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeGuard, get_args

import pytest
from baqylau_extension_testkit.data_models import JobDocument, RecordPageDocument
from baqylau_extension_testkit.lifecycle_models import CatalogDocument, HostDocument, OperationDocument, RuntimeDocument
from baqylau_extension_testkit.signoff_models import CheckpointDocument, ExtensionWorkDocument, ReportDocument

from tests import http_library_dependencies, http_test_server_runtime
from tests.extension_testkit.openapi_nodes import NO_PROPERTIES, PublishedDocument, SchemaNode

if TYPE_CHECKING:
    from pydantic import BaseModel

GET = "get"
SUCCESS_STATUSES = ("200", "201", "202")
READS = (
    ("/api/extensions", GET, CatalogDocument),
    ("/api/extensions/rescan", "post", CatalogDocument),
    ("/api/extensions/state", GET, RuntimeDocument),
    ("/api/extensions/operations/{operation_id}", GET, OperationDocument),
    ("/api/diagnostics/checkpoint", GET, CheckpointDocument),
    ("/api/diagnostics/extension-work", GET, ExtensionWorkDocument),
    ("/api/diagnostics/report", GET, ReportDocument),
    ("/api/extensions/{extension_id}/records/{collection}", GET, RecordPageDocument),
    ("/api/extensions/{extension_id}/jobs/{job_id}", GET, JobDocument),
    ("/api/extensions/{extension_id}/commands/{command_id}", "post", JobDocument),
)


@pytest.fixture
def published() -> PublishedDocument:
    """Read the host's published OpenAPI document.

    Returns:
        The document.

    """
    application = http_library_dependencies.build_web_application(http_test_server_runtime.application().instances)
    return PublishedDocument.model_validate(application.openapi())


@pytest.mark.parametrize(("path", "method", "reply"), READS)
def test_kit_fields_are_published(
    published: PublishedDocument, path: str, method: str, reply: type[HostDocument],
) -> None:
    """Each field of the kit model and of its nested kit models is a property of the host schema.

    The kit reads records, query results, and job results with the public SDK models, which the host also uses.
    """
    responses = published.paths[path][method].responses
    success = next(responses[status] for status in SUCCESS_STATUSES if status in responses)
    assert_fields(published, success.content["application/json"].media_schema, reply)


def assert_fields(published: PublishedDocument, schema: SchemaNode, model: type[BaseModel]) -> None:
    """Check the model's fields against the schema's properties, and each nested model against its schema."""
    properties = published.resolved(schema).properties or NO_PROPERTIES
    missing = set(model.model_fields) - set(properties)
    assert not missing, f"{model.__name__} reads fields that the host does not publish: {missing}"
    for name, declared in model.model_fields.items():
        nested = _nested_model(declared.annotation)
        if nested is not None:
            assert_fields(published, _object_schema(published, properties[name]), nested)


def _object_schema(published: PublishedDocument, schema: SchemaNode) -> SchemaNode:
    for choice in schema.any_of or (schema,):
        found = published.resolved(choice.item_schema or choice)
        if found.properties:
            return found
    message = f"no object schema in {schema}"
    raise AssertionError(message)


def _kinds(annotation: object) -> tuple[object, ...]:
    arguments = get_args(annotation)
    if not arguments:
        return (annotation,)
    return tuple(kind for argument in arguments for kind in _kinds(argument))


def _nested_model(annotation: object) -> type[BaseModel] | None:
    models = (kind for kind in _kinds(annotation) if _is_model(kind))
    return next(models, None)


def _is_model(kind: object) -> TypeGuard[type[BaseModel]]:
    # A public SDK model is the host's own reply model, so only the kit's models need this check.
    return isinstance(kind, type) and issubclass(kind, HostDocument)

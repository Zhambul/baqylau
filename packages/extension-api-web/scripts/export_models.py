# Copyright (c) 2026 Zhambyl Yermagambet
"""Export authoritative Python wire schemas for the public browser SDK."""

import sys
from typing import Literal

from baqylau_extension_api.models import command_results, commands, queries
from baqylau_extension_api.models.base import WireModel
from baqylau_extension_api.models.directory import DirectoryRequest, DirectorySnapshot
from baqylau_extension_api.models.documents import ContentReference, EncodedDocument, SchemaDefinition
from baqylau_extension_api.models.scopes import EntryPageCursor, ExtensionScope, SnapshotCursor
from baqylau_extension_api.terminal.models import TerminalView, TerminalViewRequest
from baqylau_extension_api.versions import API_VERSION
from pydantic import Field, JsonValue, TypeAdapter

type WebWireModels = tuple[
    ExtensionScope, EncodedDocument, SchemaDefinition, ContentReference,
    DirectoryRequest, DirectorySnapshot, SnapshotCursor, EntryPageCursor,
    TerminalViewRequest, TerminalView,
    queries.QueryRequest, queries.QueryResult,
    commands.CommandRequest, commands.CommandCancelRequest, commands.CommandCancelResult,
    commands.CommandReconcileRequest, command_results.CommandResult,
]


class ExportInfo(WireModel):
    """Name the generated SDK document without defining host routes."""

    title: str = "Baqylau extension wire models"
    version: str = API_VERSION


class ExportComponents(WireModel):
    """Contain only library-generated JSON Schema documents."""

    schemas: dict[str, JsonValue]


class ExportDocument(WireModel):
    """Use a declared OpenAPI envelope around the generated model schemas."""

    openapi: Literal["3.1.0"] = "3.1.0"
    metadata: ExportInfo = Field(default_factory=ExportInfo, alias="info")
    paths: dict[str, JsonValue] = Field(default_factory=dict)
    components: ExportComponents


def export_models() -> str:
    """Use the public models as the only source of wire field definitions.

    Returns:
        An OpenAPI schema document with no host routes or private types.

    """
    schema = TypeAdapter(WebWireModels).json_schema(ref_template="#/components/schemas/{model}")
    document = ExportDocument(components=ExportComponents(schemas=schema["$defs"]))
    return document.model_dump_json(indent=2, by_alias=True)


if __name__ == "__main__":
    sys.stdout.write(export_models())

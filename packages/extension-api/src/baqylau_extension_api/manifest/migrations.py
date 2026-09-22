# Copyright (c) 2026 Zhambyl Yermagambet
"""Declare exact supported schema changes before feature code is loaded."""

from typing import Annotated, Literal

from pydantic import Field

from baqylau_extension_api.models.base import Identifier, WireModel
from baqylau_extension_api.models.documents import SchemaRef


class MigrationPath(WireModel):
    """Pin both ends of a supported schema change, including their digests."""

    source_schema: SchemaRef
    target_schema: SchemaRef


class SettingsMigrationPath(MigrationPath):
    """Permit one exact settings conversion into the package's current schema."""

    kind: Literal["settings"] = "settings"


class RecordMigrationPath(MigrationPath):
    """Permit one exact record conversion for an owned collection."""

    kind: Literal["records"] = "records"
    collection: Identifier


type DeclaredMigrationPath = Annotated[
    SettingsMigrationPath | RecordMigrationPath, Field(discriminator="kind"),
]

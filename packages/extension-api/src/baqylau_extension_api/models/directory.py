# Copyright (c) 2026 Zhambyl Yermagambet
"""Describe peer metadata without loading peer implementation code."""

from typing import Annotated, Literal

from pydantic import Field

from baqylau_extension_api.models.base import Identifier, Revision, WireModel
from baqylau_extension_api.models.lifecycle import ExtensionInfo


class DirectoryRequest(WireModel):
    """Select active peers or the full installed catalog."""

    active_only: bool = False


class DirectoryEntry(WireModel):
    """Describe the observed state of one installed package."""

    extension_info: ExtensionInfo
    state: Literal["discovered", "disabled", "preparing", "enabled", "stopping", "failed", "incompatible"]


class DirectorySnapshot(WireModel):
    """Return peer metadata from one catalog and runtime revision."""

    catalog_revision: Revision
    runtime_revision: Identifier
    entries: Annotated[tuple[DirectoryEntry, ...], Field(max_length=1000)]

# Copyright (c) 2026 Zhambyl Yermagambet
"""Group declared contributions without callback dictionaries."""

from typing import Annotated

from pydantic import Field

from baqylau_extension_api.manifest.data import DocumentDefinition
from baqylau_extension_api.manifest.observers import DeclaredProcessingSelection
from baqylau_extension_api.manifest.operations import (
    CommandDefinition,
    PublicService,
    QueryDefinition,
    ServiceRequirement,
)
from baqylau_extension_api.manifest.processes import ProcessDeclaration
from baqylau_extension_api.manifest.views import TerminalView, WebView
from baqylau_extension_api.models.base import WireModel


class Contributions(WireModel):
    """Describe all feature registrations before a worker starts."""

    event_types: Annotated[tuple[DocumentDefinition, ...], Field(max_length=1000)] = ()
    entry_types: Annotated[tuple[DocumentDefinition, ...], Field(max_length=1000)] = ()
    source_types: Annotated[tuple[DocumentDefinition, ...], Field(max_length=1000)] = ()
    collections: Annotated[tuple[DocumentDefinition, ...], Field(max_length=1000)] = ()
    processing: Annotated[tuple[DeclaredProcessingSelection, ...], Field(max_length=5)] = ()
    queries: Annotated[tuple[QueryDefinition, ...], Field(max_length=1000)] = ()
    commands: Annotated[tuple[CommandDefinition, ...], Field(max_length=1000)] = ()
    services: Annotated[tuple[PublicService, ...], Field(max_length=100)] = ()
    consumes: Annotated[tuple[ServiceRequirement, ...], Field(max_length=100)] = ()
    web: Annotated[tuple[WebView, ...], Field(max_length=100)] = ()
    terminal: Annotated[tuple[TerminalView, ...], Field(max_length=100)] = ()
    processes: Annotated[tuple[ProcessDeclaration, ...], Field(max_length=100)] = ()
    uses_inference: bool = False
    uses_sessions: bool = False

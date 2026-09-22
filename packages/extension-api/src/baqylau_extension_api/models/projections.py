# Copyright (c) 2026 Zhambyl Yermagambet
"""Bind pure derived-data work to an exact history and prior read snapshot."""

from typing import Annotated

from pydantic import Field

from baqylau_extension_api.models.base import Revision, WireModel
from baqylau_extension_api.models.canonical import CommittedFact
from baqylau_extension_api.models.documents import Diagnostic
from baqylau_extension_api.models.events import ProcessingContext
from baqylau_extension_api.models.projection_entries import ProjectedEntry
from baqylau_extension_api.models.record_changes import RecordChange
from baqylau_extension_api.models.records import RecordKey, RecordState
from baqylau_extension_api.models.scopes import SnapshotCursor

MAX_PROJECTION_ROWS = 1000


class ProjectionBinding(WireModel):
    """Separate canonical input progress from the derived-data commit cursor."""

    context: ProcessingContext
    snapshot: SnapshotCursor
    after_input_cursor: Revision


class ProjectionSelectionRequest(WireModel):
    """Select needed record keys from captured facts without reading live data."""

    binding: ProjectionBinding
    events: Annotated[tuple[CommittedFact, ...], Field(max_length=MAX_PROJECTION_ROWS)]


class ProjectionReadSet(WireModel):
    """Name the exact keys that the host must capture at the selected snapshot."""

    binding: ProjectionBinding
    keys: Annotated[tuple[RecordKey, ...], Field(max_length=MAX_PROJECTION_ROWS)] = ()


class ProjectionRequest(ProjectionSelectionRequest):
    """Supply committed facts and each selected record, including absent keys."""

    prior_records: Annotated[tuple[RecordState, ...], Field(max_length=MAX_PROJECTION_ROWS)] = ()


class ProjectionResult(WireModel):
    """Propose ordered entries and checked record changes as one complete unit."""

    binding: ProjectionBinding
    entries: Annotated[tuple[ProjectedEntry, ...], Field(max_length=MAX_PROJECTION_ROWS)] = ()
    record_changes: Annotated[tuple[RecordChange, ...], Field(max_length=MAX_PROJECTION_ROWS)] = ()
    diagnostics: Annotated[tuple[Diagnostic, ...], Field(max_length=MAX_PROJECTION_ROWS)] = ()

# Copyright (c) 2026 Zhambyl Yermagambet
"""Read an extension's records, query results, and jobs with the public SDK's own wire models.

The public SDK owns record states, query results, command results, and
diagnostics, so the kit does not copy them.
"""

from baqylau_extension_api.models.command_results import CommandResult
from baqylau_extension_api.models.documents import Diagnostic
from baqylau_extension_api.models.observer_results import ObservationJobResult
from baqylau_extension_api.models.operations import QueryPageCursor
from baqylau_extension_api.models.queries import QueryResult
from baqylau_extension_api.models.records import RecordState
from pydantic import BaseModel, ConfigDict, RootModel

from baqylau_extension_testkit.lifecycle_models import HostDocument

# A job in one of these states does not change again.
FINAL_JOB_STATES = frozenset(("succeeded", "failed", "canceled", "outcome_unknown"))


class RecordPageDocument(HostDocument):
    """Keep one ordered record page and its continuation key."""

    records: tuple[RecordState, ...]
    next_key: str | None


class QueryReplyDocument(RootModel[QueryResult]):
    """Keep one ready or failed query result."""


class JobDocument(HostDocument):
    """Keep one job's state and its last result."""

    job_id: str
    kind: str
    state: str
    revision: int
    result: CommandResult | ObservationJobResult | None = None
    diagnostic: Diagnostic | None = None


class QueryRequest(BaseModel):
    """Select one declared query with its scope, JSON arguments, and page."""

    model_config = ConfigDict(frozen=True)

    scope: str
    arguments: str
    page: QueryPageCursor | None = None


class CommandRequest(BaseModel):
    """Submit one command with its scope, JSON arguments, and stable request key."""

    model_config = ConfigDict(frozen=True)

    scope: str
    request_key: str
    arguments: str
    expected_state_revision: str | None = None

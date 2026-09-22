# Copyright (c) 2026 Zhambyl Yermagambet
"""Separate extension reads from accepted jobs and uncertain write recovery."""

from typing import Protocol, runtime_checkable

from baqylau_extension_api.models.command_results import CommandResult
from baqylau_extension_api.models.commands import (
    CommandCancelRequest,
    CommandCancelResult,
    CommandReconcileRequest,
    CommandRequest,
)
from baqylau_extension_api.models.queries import QueryRequest, QueryResult


@runtime_checkable
class ExtensionQueries(Protocol):
    """Read bounded data without accepting an external write."""

    def query(self, query_request: QueryRequest) -> QueryResult:
        """Return a checked result from one explicit state snapshot."""


@runtime_checkable
class ExtensionCommands(Protocol):
    """Run only durable jobs accepted by the host command service."""

    def execute(self, command_request: CommandRequest) -> CommandResult:
        """Run the selected job once and report its known or uncertain outcome."""

    def cancel(self, cancel_request: CommandCancelRequest) -> CommandCancelResult:
        """Request a stop without claiming that it reverses an external effect."""

    def reconcile(self, reconcile_request: CommandReconcileRequest) -> CommandResult:
        """Determine an uncertain result without repeating the original write."""

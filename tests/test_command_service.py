# Copyright (c) 2026 Zhambyl Yermagambet
"""Accept and run durable commands against one active package double."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

import pytest
from baqylau_extension_api.contracts.plugin import ExtensionCapabilities, ExtensionPlugin
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models import scopes
from baqylau_extension_api.models.command_results import CommandResult, CommandSucceeded
from baqylau_extension_api.models.commands import CommandCancelResult
from baqylau_extension_api.models.directory import DirectoryEntry
from baqylau_extension_api.models.documents import EncodedDocument
from baqylau_extension_api.models.lifecycle import ExtensionInfo

from api.extensions import command_models, command_service, job_models
from extensions.control_policy import ExtensionControlPolicy, ExtensionReadOnlyError
from extensions.registry_package import RegistryPackage
from repository.contract.extension_jobs import CommandJobRequest
from tests import sqlite_repository_dependencies as repository_dependencies
from tests.extension_api import operation_samples, service_samples

if TYPE_CHECKING:
    from baqylau_extension_api.contracts.lifecycle import ExtensionLifecycle
    from baqylau_extension_api.models.commands import (
        CommandBinding,
        CommandCancelRequest,
        CommandReconcileRequest,
        CommandRequest,
    )

OWNER = operation_samples.OWNER
COMMAND_ID = operation_samples.COMMAND_ID
SCOPE = scopes.InstallationScope()
REQUEST_KEY = "request-1"
ARGUMENTS = '"input"'


class FakeCommands:
    """Record one execution and answer with a checked success."""

    def __init__(
        self,
        cancel_status: Literal["requested", "canceled", "not_running", "outcome_unknown"] = "canceled",
    ) -> None:
        """Start with no executed requests and one selected cancel status."""
        self.requests: list[CommandRequest] = []
        self.cancel_status = cancel_status

    def execute(self, request: CommandRequest) -> CommandSucceeded:
        """Record the request and return a result in the declared schema.

        Returns:
            A success with a document in the command result schema.

        """
        self.requests.append(request)
        return CommandSucceeded(
            binding=request.binding,
            document=EncodedDocument(schema_ref=operation_samples.schema_definition().reference, json_text='"ok"'),
        )

    def cancel(self, request: CommandCancelRequest) -> CommandCancelResult:
        """Answer the selected cancellation status for the exact attempt.

        Returns:
            The checked cancellation result.

        """
        return CommandCancelResult(binding=request.binding, status=self.cancel_status)

    def reconcile(self, request: CommandReconcileRequest) -> CommandResult:
        """Answer one checked success for the reconciled attempt.

        Returns:
            A success with a document in the command result schema.

        """
        return CommandSucceeded(
            binding=request.command.binding,
            document=EncodedDocument(schema_ref=operation_samples.schema_definition().reference, json_text='"ok"'),
        )


@dataclass(frozen=True)
class FakePlugin:
    """Carry a checked identity and the command capability."""

    extension_info: ExtensionInfo
    capabilities: ExtensionCapabilities


def a_package(commands: FakeCommands, *, effect: Literal["read", "write"] = "read") -> RegistryPackage:
    """Build an enabled package double with the command capability.

    Returns:
        The active package selection.

    """
    environment = service_samples.environment(OWNER)
    capabilities = ExtensionCapabilities(
        lifecycle=cast("ExtensionLifecycle", None), commands=commands,
    )
    plugin = cast("ExtensionPlugin", FakePlugin(
        extension_info=environment.extension_info, capabilities=capabilities,
    ))
    return RegistryPackage(
        manifest=operation_samples.manifest(effect=effect),
        entry=DirectoryEntry(extension_info=environment.extension_info, state="enabled"),
        environment=environment,
        plugin=plugin,
    )


def a_request(request_key: str = REQUEST_KEY) -> command_models.ExtensionCommandRequest:
    """Build one command submission.

    Returns:
        The typed command request.

    """
    return command_models.ExtensionCommandRequest(
        scope=SCOPE.model_dump_json(), request_key=request_key, arguments=ARGUMENTS,
    )


def dispatch(
    main: repository_dependencies.SqliteDatabase,
    commands: FakeCommands,
    *,
    effect: Literal["read", "write"] = "read",
    policy: ExtensionControlPolicy | None = None,
) -> command_service.CommandDispatch:
    """Build the dispatch over the fake package and the real job store.

    Returns:
        The command dispatch.

    """
    return command_service.CommandDispatch(
        packages=(a_package(commands, effect=effect),),
        jobs=repository_dependencies.SqliteExtensionJobRepository(main),
        policy=ExtensionControlPolicy() if policy is None else policy,
    )


def test_command_runs_and_stores_its_result(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """A command executes once and stores its checked success."""
    commands = FakeCommands()
    job = command_service.run_command(dispatch(main, commands), OWNER, COMMAND_ID, SCOPE, a_request())

    assert job.state == "succeeded"
    assert job.result is not None
    assert len(commands.requests) == 1


def test_command_replays_by_request_key(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """A repeated request key returns the stored job and does not run again."""
    commands = FakeCommands()
    command_dispatch = dispatch(main, commands)
    first = command_service.run_command(command_dispatch, OWNER, COMMAND_ID, SCOPE, a_request())
    second = command_service.run_command(command_dispatch, OWNER, COMMAND_ID, SCOPE, a_request())

    assert first.job_id == second.job_id
    assert second.state == "succeeded"
    assert len(commands.requests) == 1


def test_write_command_rejects_a_read_only_host(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """A write command does not run when the host is read-only."""
    commands = FakeCommands()
    command_dispatch = dispatch(
        main, commands, effect="write", policy=ExtensionControlPolicy(read_only=True),
    )
    request = a_request().model_copy(update={"expected_state_revision": "state-1"})

    with pytest.raises(ExtensionReadOnlyError):
        command_service.run_command(command_dispatch, OWNER, COMMAND_ID, SCOPE, request)
    assert not commands.requests


def test_read_command_ignores_the_write_policy(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """A read command runs even when the host is read-only."""
    commands = FakeCommands()
    command_dispatch = dispatch(main, commands, policy=ExtensionControlPolicy(read_only=True))
    job = command_service.run_command(command_dispatch, OWNER, COMMAND_ID, SCOPE, a_request())

    assert job.state == "succeeded"
    assert len(commands.requests) == 1


def seed_accepted(main: repository_dependencies.SqliteDatabase, binding: CommandBinding) -> None:
    """Store one accepted command job for the supplied binding."""
    repository_dependencies.SqliteExtensionJobRepository(main).accept_command(CommandJobRequest(
        owner=OWNER,
        scope=SCOPE,
        job_id=binding.job_id,
        request_key=binding.request_key,
        binding=binding.model_dump_json(),
        request="{}",
    ))


def test_cancel_command_marks_a_proven_stop(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """A proven stop sets the stored job state to canceled."""
    commands = FakeCommands(cancel_status="canceled")
    command_dispatch = dispatch(main, commands)
    binding = operation_samples.command_request().binding
    seed_accepted(main, binding)

    response = command_service.cancel_command(
        command_dispatch,
        OWNER,
        binding.job_id,
        SCOPE,
        job_models.ExtensionJobCancelRequest(
            scope=SCOPE.model_dump_json(), expected_revision=1, reason="stop",
        ),
    )

    assert (response.status, response.revision) == ("canceled", 2)
    stored = command_dispatch.jobs.read(OWNER, SCOPE, binding.job_id)
    assert stored is not None
    assert stored.state == "canceled"


def test_cancel_command_rejects_a_stale_revision(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """A cancellation from an old revision is refused."""
    command_dispatch = dispatch(main, FakeCommands())
    binding = operation_samples.command_request().binding
    seed_accepted(main, binding)

    with pytest.raises(command_service.JobRevisionError):
        command_service.cancel_command(
            command_dispatch,
            OWNER,
            binding.job_id,
            SCOPE,
            job_models.ExtensionJobCancelRequest(
                scope=SCOPE.model_dump_json(), expected_revision=99, reason="stop",
            ),
        )


def test_cancel_command_reports_a_missing_job(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """An absent job identity is a not-found error."""
    command_dispatch = dispatch(main, FakeCommands())

    with pytest.raises(command_service.JobNotFoundError):
        command_service.cancel_command(
            command_dispatch,
            OWNER,
            "missing",
            SCOPE,
            job_models.ExtensionJobCancelRequest(
                scope=SCOPE.model_dump_json(), expected_revision=1, reason="stop",
            ),
        )


def seed_with_request(main: repository_dependencies.SqliteDatabase, request: CommandRequest) -> None:
    """Store one accepted command job with its complete request document."""
    binding = request.binding
    repository_dependencies.SqliteExtensionJobRepository(main).accept_command(CommandJobRequest(
        owner=OWNER,
        scope=SCOPE,
        job_id=binding.job_id,
        request_key=binding.request_key,
        binding=binding.model_dump_json(),
        request=request.model_dump_json(),
    ))


def test_reconcile_command_stores_a_resolved_outcome(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """Reconciliation stores the checked outcome without running the command again."""
    commands = FakeCommands()
    command_dispatch = dispatch(main, commands)
    request = operation_samples.command_request()
    seed_with_request(main, request)

    job = command_service.reconcile_command(
        command_dispatch,
        OWNER,
        request.binding.job_id,
        SCOPE,
        job_models.ExtensionJobReconcileRequest(scope=SCOPE.model_dump_json(), expected_revision=1),
    )

    assert job.state == "succeeded"
    assert job.result is not None
    assert not commands.requests


def test_command_reports_a_missing_declaration(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """An undeclared command is not accepted."""
    commands = FakeCommands()
    command_dispatch = dispatch(main, commands)

    with pytest.raises(ExtensionContractError):
        command_service.run_command(command_dispatch, OWNER, f"{OWNER}.missing", SCOPE, a_request())
    assert not commands.requests

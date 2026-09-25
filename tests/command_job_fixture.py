# Copyright (c) 2026 Zhambyl Yermagambet
"""Build command dispatch and job control over the package double and the real job stores."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Literal

from api.extensions import command_service
from domain.ids import ExtensionJobId
from extensions.control_policy import ExtensionControlPolicy
from extensions.job_control import JobControl
from extensions.observer_models import ObserverStores
from repository.contract.extension_jobs import CommandJobRequest
from tests import command_package_fixture as packages, sqlite_repository_dependencies as repository_dependencies

if TYPE_CHECKING:
    from baqylau_extension_api.models.commands import CommandBinding

EMPTY_REQUEST = "{}"


def job_store(main: repository_dependencies.SqliteDatabase) -> repository_dependencies.SqliteExtensionJobRepository:
    """Build the real job store.

    Returns:
        The job store over the test database.

    """
    return repository_dependencies.SqliteExtensionJobRepository(main)


def dispatch(
    main: repository_dependencies.SqliteDatabase,
    fake_commands: packages.FakeCommands,
    *,
    effect: Literal["read", "write"] = "read",
    policy: ExtensionControlPolicy | None = None,
) -> command_service.CommandDispatch:
    """Build the dispatch over the fake package and the real job store.

    Returns:
        The command dispatch.

    """
    return command_service.CommandDispatch(
        packages=(packages.a_package(fake_commands, effect=effect),),
        jobs=job_store(main),
        policy=ExtensionControlPolicy() if policy is None else policy,
    )


def repository_dispatch(
    main: repository_dependencies.SqliteDatabase, fake_commands: packages.FakeCommands,
) -> command_service.CommandDispatch:
    """Build the dispatch over the package double with its command declared for the repository scope.

    Returns:
        The command dispatch.

    """
    base = packages.a_package(fake_commands)
    contributions = base.manifest.contributions
    definition = contributions.commands[0].model_copy(update={"scopes": ("repository",)})
    commands_update = contributions.model_copy(update={"commands": (definition,)})
    package = replace(base, manifest=base.manifest.model_copy(update={"contributions": commands_update}))
    return command_service.CommandDispatch((package,), job_store(main))


def stores(main: repository_dependencies.SqliteDatabase) -> ObserverStores:
    """Build the real job and observer stores.

    Returns:
        The stores over the test database.

    """
    observers = repository_dependencies.SqliteExtensionObserverRepository(main)
    return ObserverStores(observers=observers, jobs=job_store(main))


def control(main: repository_dependencies.SqliteDatabase, fake_commands: packages.FakeCommands) -> JobControl:
    """Build job control over the fake command package and the real stores.

    Returns:
        The job control with no committed manager.

    """
    package = packages.a_package(fake_commands)
    return JobControl(packages=(package,), stores=stores(main), manager_id=None)


def seed_job(
    main: repository_dependencies.SqliteDatabase, binding: CommandBinding, request_document: str = EMPTY_REQUEST,
) -> None:
    """Store one accepted command job for the supplied binding and request document."""
    job_store(main).accept_command(CommandJobRequest(
        owner=packages.OWNER,
        scope=packages.SCOPE,
        job_id=ExtensionJobId(binding.job_id),
        request_key=binding.request_key,
        binding=binding.model_dump_json(),
        request=request_document,
    ))

# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide harness session storage and event gateways."""

from typing import Annotated

from fastapi import Depends

from app import (
    provider_audit_storage,
    provider_databases as database_providers,
    provider_fact_storage as fact_providers,
    provider_harness_registry as registry_providers,
)
from app.injection import singleton
from harness.hooks import gateway as hook_gateway_service, recorder as hook_recorders
from harness.services import telemetry
from repository.contract import sessions as session_contract
from repository.impl.sqlite import sessions as sqlite_sessions


@singleton
def sessions(
    database: database_providers.MainDb,
    harnesses: registry_providers.Registry,
) -> session_contract.SessionRepository:
    """Return harness-aware session storage.

    Returns:
        Harness-aware session storage.

    """
    return sqlite_sessions.SqliteSessionRepository(database, harnesses)


Sessions = Annotated[session_contract.SessionRepository, Depends(sessions)]


@singleton
def hook_recorder(
    raw: fact_providers.RawEvents,
    audit: provider_audit_storage.Recorder,
) -> hook_recorders.OrderedHookRecorder:
    """Return the one writer of hook raw events.

    Returns:
        The recorder that writes hook deliveries in arrival order.

    """
    return hook_recorders.OrderedHookRecorder(raw, audit)


HookRecorder = Annotated[hook_recorders.OrderedHookRecorder, Depends(hook_recorder)]


@singleton
def hook_gateway(
    harnesses: registry_providers.Registry,
    recorder: HookRecorder,
) -> hook_gateway_service.HookGatewayService:
    """Return the harness hook event gateway.

    Returns:
        Harness hook event gateway.

    """
    return hook_gateway_service.HookGatewayService(harnesses, recorder)


HookGateway = Annotated[
    hook_gateway_service.HookGatewayService,
    Depends(hook_gateway),
]


@singleton
def telemetry_gateway(
    harnesses: registry_providers.Registry,
    raw: fact_providers.RawEvents,
    session_storage: Sessions,
) -> telemetry.TelemetryGatewayService:
    """Return the harness telemetry event gateway.

    Returns:
        Harness telemetry event gateway.

    """
    return telemetry.TelemetryGatewayService(harnesses, raw, session_storage)


TelemetryGateway = Annotated[
    telemetry.TelemetryGatewayService,
    Depends(telemetry_gateway),
]

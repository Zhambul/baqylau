# Copyright (c) 2026 Zhambyl Yermagambet
"""Run bounded candidate preparation outside the engine and manager mutex."""

from contextlib import ExitStack, closing
from threading import Event

from extensions.manager_directory import include_inactive
from extensions.manager_resources import ManagerServices, PreparationOutcome
from extensions.models.catalog import ExtensionCatalogSnapshot
from extensions.models.lifecycle_operations import LifecycleFailure, LifecycleOperation
from extensions.prepared_runtime import OwnedPreparedRuntime
from extensions.runtime_preparation_contract import PreparedExtensionRuntime, RuntimePreparationStoppedError


def prepare_operation(
    services: ManagerServices, operation: LifecycleOperation, catalog: ExtensionCatalogSnapshot, stop_requested: Event,
) -> PreparationOutcome:
    """Catch normal preparation failures without exposing feature logs or settings.

    Returns:
        A complete owned set or a failure that the engine boundary can store.

    """
    try:
        prepared = _prepare_directory(services, operation, catalog, stop_requested)
    except RuntimePreparationStoppedError:
        return PreparationOutcome(failure=LifecycleFailure(
            code="interrupted", detail="The manager stopped runtime preparation.",
        ))
    except Exception:  # noqa: BLE001 -- Persist bounded failure and preserve the current active set.
        return PreparationOutcome(failure=LifecycleFailure(
            code="preparation_failed", detail="The complete extension runtime could not be prepared.",
        ))
    return PreparationOutcome(prepared=prepared)


def _prepare_directory(
    services: ManagerServices, operation: LifecycleOperation, catalog: ExtensionCatalogSnapshot, stop_requested: Event,
) -> PreparedExtensionRuntime:
    with ExitStack() as cleanup:
        prepared = cleanup.enter_context(closing(services.preparation.prepare_runtime(
            operation.proposal.candidate, stop_requested,
        )))
        snapshot = include_inactive(prepared.snapshot, catalog)
        return OwnedPreparedRuntime(snapshot, cleanup.pop_all(), prepared.resolution)

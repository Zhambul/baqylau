# Copyright (c) 2026 Zhambyl Yermagambet
"""Build complete ordered candidates from retained state and checked user selection."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.activation import activation_order
from baqylau_extension_api.models.base import ExtensionId
from baqylau_extension_api.schemas import SchemaSet

from extensions.lifecycle_control_contract import LifecycleRequestError
from extensions.lifecycle_plan_reads import discovered_package
from extensions.lifecycle_plan_resources import CheckedLifecyclePlan, LifecyclePlanningState, SelectedPackage
from extensions.models import record_migration, settings, settings_migration
from extensions.models.lifecycle_requests import LifecyclePlan, LifecyclePlanRequest
from extensions.models.lifecycle_selection import ExtensionIntent
from extensions.models.runtime_candidates import MigratingRuntimePackage, RuntimePackageCandidate
from extensions.registry_removal import removal_order


def plan_lifecycle(
    state: LifecyclePlanningState, extension_id: ExtensionId, request: LifecyclePlanRequest,
) -> CheckedLifecyclePlan:
    """Check the whole graph and settings before preparation or stored admission.

    Returns:
        One complete candidate and its public affected-owner preview.

    """
    if request.action == "disable":
        return _removal(state, extension_id, request)
    return _selection(state, extension_id, request)


def _selection(
    state: LifecyclePlanningState, extension_id: ExtensionId, request: LifecyclePlanRequest,
) -> CheckedLifecyclePlan:
    present = any(package.manifest.extension_id == extension_id for package in state.selected)
    if present != (request.action == "reload"):
        message = "enable requires an inactive package; reload requires a committed active package"
        raise LifecycleRequestError(message)
    selected = discovered_package(state, extension_id, request.package_digest)
    packages = tuple(package for package in state.selected if package.manifest.extension_id != extension_id)
    return CheckedLifecyclePlan(
        LifecyclePlan(extension_id=extension_id, request=request, affected_extensions=(extension_id,)),
        _checked_order(state, (*packages, selected)),
        (ExtensionIntent(extension_id=extension_id, enabled=True, package_digest=request.package_digest),),
    )


def _removal(
    state: LifecyclePlanningState, extension_id: ExtensionId, request: LifecyclePlanRequest,
) -> CheckedLifecyclePlan:
    affected = _removed_owners(state, extension_id)
    remaining = tuple(package for package in state.selected if package.manifest.extension_id not in affected)
    return CheckedLifecyclePlan(
        LifecyclePlan(extension_id=extension_id, request=request, affected_extensions=affected),
        _checked_order(state, remaining),
        tuple(ExtensionIntent(extension_id=owner, enabled=False) for owner in affected),
    )


def _removed_owners(state: LifecyclePlanningState, extension_id: ExtensionId) -> tuple[str, ...]:
    manifests = tuple(package.manifest for package in state.selected)
    if any(manifest.extension_id == extension_id for manifest in manifests):
        return removal_order(manifests, (extension_id,))
    intents = state.manager.lifecycle.intents
    if any(intent.extension_id == extension_id and intent.enabled for intent in intents):
        return (extension_id,)
    message = "disable requires a committed active package or an incomplete enable request"
    raise LifecycleRequestError(message)


def _checked_order(
    state: LifecyclePlanningState, selected: tuple[SelectedPackage, ...],
) -> tuple[RuntimePackageCandidate, ...]:
    try:
        return _validate_packages(state, selected)
    except ExtensionContractError as error:
        # SDK contract checks run in the host and name the rule, for example a dependency cycle.
        message = f"the selected extension set is not valid: {error}"
        raise LifecycleRequestError(message) from error
    except ValueError as error:
        message = "the selected extension set has incompatible dependencies, contributions, or settings"
        raise LifecycleRequestError(message) from error


def _validate_packages(
    state: LifecyclePlanningState, selected: tuple[SelectedPackage, ...],
) -> tuple[RuntimePackageCandidate, ...]:
    order = activation_order(tuple(package.manifest for package in selected))
    schemas = SchemaSet(tuple(schema for package in selected for schema in package.manifest.schemas))
    return tuple(_package_candidate(state, entry, schemas) for owner in order for entry in selected
        if entry.manifest.extension_id == owner
    )


def _package_candidate(
    state: LifecyclePlanningState, package: SelectedPackage, schemas: SchemaSet,
) -> RuntimePackageCandidate:
    source = next((entry.settings for entry in state.manager.lifecycle.settings
                   if entry.extension_id == package.manifest.extension_id), settings.SettingsOverrides())
    candidate: RuntimePackageCandidate = package.selection
    records = record_migration.record_sources(package.manifest, state.record_schemas)
    if records or settings_migration.needs_settings_migration(package.manifest, source):
        candidate = MigratingRuntimePackage(
            extension_info=package.selection.extension_info, source=source, records=records,
        )
    candidate.validate_manifest(package.manifest, schemas)
    return candidate

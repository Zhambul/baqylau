# Copyright (c) 2026 Zhambyl Yermagambet
"""Admit schema-checked settings through the same runtime manager as lifecycle changes."""

from dataclasses import dataclass, field
from functools import partial

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.base import ExtensionId, Identifier

from extensions import control_admission, lifecycle_plan_reads, settings_planner, settings_views
from extensions.control_policy import ExtensionControlPolicy, require_extension_write
from extensions.lifecycle_control_contract import LifecycleRequestError
from extensions.manager_contract import ExtensionManager
from extensions.models import lifecycle_operations, lifecycle_state, settings_requests, settings_view
from extensions.settings_control_contract import ExtensionSettingsControl
from repository.contract.extension_catalog import ExtensionCatalogRepository


@dataclass(frozen=True)
class SettingsControl(ExtensionSettingsControl):
    """Keep uncommitted values out of reads and failed values out of accepted storage."""

    manager: ExtensionManager | None
    catalog: ExtensionCatalogRepository
    policy: ExtensionControlPolicy = field(default_factory=ExtensionControlPolicy)

    def read_settings(
        self, extension_id: ExtensionId, request: settings_requests.SettingsReadRequest,
    ) -> settings_view.SettingsSnapshot:
        """Resolve one accepted scope without a worker call.

        Returns:
            Settings bound to the committed package, or current inactive discovery.

        Raises:
            LifecycleRequestError: If stored documents do not match the selected schema.

        """
        manager = control_admission.require_manager(self.manager)
        state = lifecycle_plan_reads.read_planning_state(manager.read_state(), self.catalog)
        try:
            return settings_views.read_settings_view(state, extension_id, request)
        except ExtensionContractError as error:
            message = "accepted settings do not match the selected package schema"
            raise LifecycleRequestError(message) from error

    def change_settings(
        self, extension_id: ExtensionId, request: settings_requests.SettingsRequest,
    ) -> lifecycle_state.LifecycleAdmission:
        """Check write policy and exact retry before preparing a complete candidate.

        Returns:
            Durable admission, not proof of completed settings activation.

        """
        require_extension_write(self.policy)
        origin = settings_requests.SettingsRequestOrigin(extension_id=extension_id, request=request)
        return control_admission.submit_request(
            control_admission.require_manager(self.manager), origin, partial(self._prepare, origin),
        )

    def _prepare(
        self, origin: settings_requests.SettingsRequestOrigin, operation_id: Identifier,
    ) -> lifecycle_operations.LifecycleProposal:
        manager = control_admission.require_manager(self.manager)
        state = lifecycle_plan_reads.planning_state(manager.read_state(), self.catalog, origin.request)
        try:
            return settings_planner.plan_settings(state, origin, operation_id)
        except ExtensionContractError as error:
            message = "settings change does not match the selected package schema"
            raise LifecycleRequestError(message) from error

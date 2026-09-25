# Copyright (c) 2026 Zhambyl Yermagambet
"""Translate confirmed user requests into complete manager-owned runtime proposals."""

from dataclasses import dataclass, field
from functools import partial

from baqylau_extension_api.models.base import ExtensionId

from extensions import control_admission, lifecycle_plan_reads, lifecycle_planner
from extensions.control_policy import ExtensionControlPolicy, require_extension_write
from extensions.lifecycle_control_contract import (
    ExtensionLifecycleControl,
    LifecycleConflictError,
    LifecycleUnavailableError,
)
from extensions.lifecycle_plan_resources import CheckedLifecyclePlan
from extensions.manager_contract import ExtensionManager
from extensions.models import (
    lifecycle_operations as operations,
    lifecycle_requests as requests,
    lifecycle_state,
    record_migration,
)
from extensions.models.runtime_candidates import runtime_candidate
from repository.contract.extension_catalog import ExtensionCatalogRepository
from repository.contract.record_migrations import StoredRecordSchemaReader


@dataclass(frozen=True)
class LifecycleControl(ExtensionLifecycleControl):
    """Use one owned manager, with no direct worker, registry, or database access."""

    manager: ExtensionManager | None
    catalog: ExtensionCatalogRepository
    policy: ExtensionControlPolicy = field(default_factory=ExtensionControlPolicy)
    records: StoredRecordSchemaReader | None = None

    def preview_lifecycle(
        self, extension_id: ExtensionId, request: requests.LifecyclePlanRequest,
    ) -> requests.LifecyclePlan:
        """Validate the selected graph without accepting or preparing a change.

        Returns:
            Exact affected owners for explicit dependent confirmation.

        """
        state = lifecycle_plan_reads.planning_state(
            self._owner.read_state(), self.catalog, request, self._schemas(),
        )
        return lifecycle_planner.plan_lifecycle(state, extension_id, request).public

    def change_lifecycle(
        self, extension_id: ExtensionId, request: requests.LifecycleRequest,
    ) -> lifecycle_state.LifecycleAdmission:
        """Replay the original request or prepare a newly confirmed complete candidate.

        Returns:
            Durable admission, not a claim that the package is already enabled.

        """
        require_extension_write(self.policy)
        origin = requests.LifecycleRequestOrigin(extension_id=extension_id, request=request)
        return control_admission.submit_request(self._owner, origin, partial(self._prepare, origin))

    def disable_failed(self, extension_id: ExtensionId, request_id: str) -> lifecycle_state.LifecycleAdmission:
        """Disable an extension that failed too many consecutive calls, with its required dependents.

        The host makes this change, so it confirms the exact dependents and is
        not a user write under the read-only policy.

        Returns:
            Durable admission of the recorded failure operation.

        """
        state = self._owner.read_state()
        plan = requests.LifecyclePlanRequest(
            action="disable", expected_revision=state.lifecycle.revision,
            expected_catalog_revision=self.catalog.read_extension_catalog().revision,
        )
        affected = self.preview_lifecycle(extension_id, plan).affected_extensions
        request = requests.LifecycleRequest(
            action=plan.action, expected_revision=plan.expected_revision,
            expected_catalog_revision=plan.expected_catalog_revision, request_id=request_id,
            confirmed_dependents=tuple(owner for owner in affected if owner != extension_id),
        )
        origin = requests.LifecycleRequestOrigin(extension_id=extension_id, request=request)
        return control_admission.submit_request(self._owner, origin, partial(self._prepare, origin, failure=True))

    @property
    def _owner(self) -> ExtensionManager:
        """The daemon-owned manager, checked when a request uses the service."""
        return control_admission.require_manager(self.manager)

    def _schemas(self) -> tuple[record_migration.StoredRecordSchema, ...]:
        return () if self.records is None else self.records.stored_record_schemas()

    def _prepare(
        self, origin: requests.LifecycleRequestOrigin, operation_id: str, *, failure: bool = False,
    ) -> operations.LifecycleProposal:
        state = lifecycle_plan_reads.planning_state(
            self._owner.read_state(), self.catalog, origin.request, self._schemas(),
        )
        planned = lifecycle_planner.plan_lifecycle(state, origin.extension_id, requests.plan_request(origin.request))
        _require_confirmation(planned, origin)
        manager_id = state.manager.lifecycle.manager_id
        if manager_id is None:
            message = "the extension manager has no stored ownership claim"
            raise LifecycleUnavailableError(message)
        return operations.LifecycleProposal(
            operation_id=operation_id, manager_id=manager_id,
            expected_revision=origin.request.expected_revision,
            kind="failure" if failure else origin.request.action,
            candidate=runtime_candidate(
                runtime_revision=f"runtime-{operation_id}", catalog_revision=origin.request.expected_catalog_revision,
                packages=planned.packages,
            ), intents=planned.intents, request_origin=origin,
        )


def _require_confirmation(planned: CheckedLifecyclePlan, origin: requests.LifecycleRequestOrigin) -> None:
    expected = {owner for owner in planned.public.affected_extensions if owner != origin.extension_id}
    if set(origin.request.confirmed_dependents) != expected:
        message = "confirm the exact required dependents from the lifecycle preview"
        raise LifecycleConflictError(message)

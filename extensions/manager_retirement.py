# Copyright (c) 2026 Zhambyl Yermagambet
"""Release removed workers only after borrowed reads have left the active registry."""

from dataclasses import dataclass
from typing import Literal

from baqylau_extension_api.models.lifecycle import DeactivationRequest

from extensions.models.cleanup import RetirementIssue, RuntimeShutdown
from extensions.registry_package import RegistryPackage
from extensions.runtime_preparation_contract import PreparedExtensionRuntime


@dataclass
class RetirementOwner:
    """Retain uncertainty separately from the resource owner of a drained runtime."""

    prepared: PreparedExtensionRuntime
    revision: str
    pending: tuple[RegistryPackage, ...]
    closed: bool = False
    close_issue: RetirementIssue | None = None
    issues: tuple[RetirementIssue, ...] = ()

    def retire(self, reason: Literal["replace", "shutdown"]) -> tuple[RetirementIssue, ...]:
        """Release only acknowledged work, then close the owned process resources.

        Returns:
            Remaining uncertainty; an empty tuple means cleanup completed.

        """
        self.deactivate(reason)
        if not self.issues:
            self.close_resources()
        return self.observation().issues

    def deactivate(self, reason: Literal["replace", "shutdown"]) -> None:
        """Retry only unacknowledged workers while their resources remain available."""
        if self.closed or self.close_issue is not None:
            return
        attempted = tuple(
            (package, _deactivate(package, self.revision, reason)) for package in self.pending
        )
        self.pending = tuple(package for package, issue in attempted if issue is not None)
        self.issues = tuple(issue for _, issue in attempted if issue is not None)

    def close_resources(self) -> None:
        """Close a drained runtime once, without changing its unresolved-job evidence."""
        if self.closed or self.close_issue is not None:
            return
        try:
            self.prepared.close()
        except Exception:  # noqa: BLE001 -- A failed close is not proof that all resources stopped.
            self.close_issue = RetirementIssue(runtime_revision=self.revision, reason="close_failed")
        else:
            self.closed = True

    def observation(self) -> RuntimeShutdown:
        """Read evidence without access to a closed worker or prepared snapshot.

        Returns:
            Physical closure and unresolved work as separate facts.

        """
        close_issues = () if self.close_issue is None else (self.close_issue,)
        return RuntimeShutdown(
            runtime_revision=self.revision, resources_closed=self.closed,
            issues=self.issues + close_issues,
        )


def retirement_owner(prepared: PreparedExtensionRuntime) -> RetirementOwner:
    """Capture removal order before cleanup can make snapshot access unavailable.

    Returns:
        One owner with dependents before their providers.

    """
    snapshot = prepared.snapshot
    pending = tuple(package for owner in reversed(snapshot.active_order) for package in snapshot.packages
                    if package.manifest.extension_id == owner)
    return RetirementOwner(prepared, snapshot.directory.runtime_revision, pending)


def retire_all(
    retired: tuple[RetirementOwner, ...], reason: Literal["replace", "shutdown"],
) -> tuple[RetirementIssue, ...]:
    """Run outside the manager mutex and keep unresolved owners for an explicit retry.

    Returns:
        Complete cleanup issues from the removed set.

    """
    return tuple(issue for owner in retired for issue in owner.retire(reason))


def _deactivate(
    package: RegistryPackage, revision: str, reason: Literal["replace", "shutdown"],
) -> RetirementIssue | None:
    if package.plugin is None:
        return None
    try:
        response = package.plugin.capabilities.lifecycle.deactivate(DeactivationRequest(
            runtime_revision=revision, reason=reason,
        ))
    except Exception:  # noqa: BLE001 -- Retain the owner rather than discard uncertain external work.
        return RetirementIssue(
            runtime_revision=revision, extension_id=package.manifest.extension_id, reason="deactivation_failed",
        )
    if response.runtime_revision != revision:
        return RetirementIssue(
            runtime_revision=revision, extension_id=package.manifest.extension_id, reason="deactivation_failed",
        )
    if response.pending_job_ids:
        return RetirementIssue(
            runtime_revision=revision, extension_id=package.manifest.extension_id, reason="unresolved_jobs",
            pending_job_ids=response.pending_job_ids,
        )
    return None

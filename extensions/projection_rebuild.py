# Copyright (c) 2026 Zhambyl Yermagambet
"""Rebuild one owner's projection into a candidate generation from stored facts."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from baqylau_extension_api.models.documents import Diagnostic

from extensions import projector_packages, transformer_packages
from extensions.pass_health import PassHealth
from extensions.projection_pass import ProjectionPass
from repository.contract.extension_records import RecordStateReader
from repository.contract.projection_generations import (
    GenerationState,
    ProjectionGeneration,
    ProjectionGenerationRepository,
)

if TYPE_CHECKING:
    from extensions.registry_package import RegistryPackage

REBUILD_SCOPE_LIMIT = 100
FAILED_CODE = "host.projection_rebuild_failed"
FAILED_MESSAGE = "A projector or transform call failed or was rejected during the rebuild."


class RebuildRefusedError(LookupError):
    """Reject a rebuild of an owner that has no active projector."""


@dataclass(frozen=True)
class ProjectionRebuild:
    """Build candidate generations with the live projection code, one bounded pass at a time.

    The rebuild calls only pure projectors and projection transforms. It never
    runs observers, commands, notifications, processes, or inference, and it
    writes only the candidate's mirror rows, so live views do not change until
    an explicit switch.
    """

    live: ProjectionPass
    generations: ProjectionGenerationRepository
    candidate_reader: Callable[[str], RecordStateReader]
    scope_limit: int = REBUILD_SCOPE_LIMIT

    def request(self, owner: str, registry_packages: Sequence[RegistryPackage]) -> ProjectionGeneration:
        """Start one candidate generation for an owner with an active projector.

        Returns:
            The new building generation.

        Raises:
            RebuildRefusedError: If the owner has no active projector.

        """
        active = projector_packages.projector_packages(registry_packages)
        if not any(package.extension_id == owner for package in active):
            message = "the extension has no active projector to rebuild"
            raise RebuildRefusedError(message)
        return self.generations.create(owner, self.live.history_revision)

    def build_pending(self, registry_packages: Sequence[RegistryPackage], health: PassHealth) -> int:
        """Advance every building generation by one bounded pass.

        A generation with nothing pending becomes ready with its comparison. A
        failed call fails the generation and keeps the live one. A generation
        whose owner is not active waits.

        Returns:
            The number of facts read, so the caller can schedule a continuation.

        """
        packages = projector_packages.projector_packages(registry_packages)
        transformers = transformer_packages.projection_transformer_packages(registry_packages)
        total = 0
        for generation in self.generations.generations_in_state(GenerationState.BUILDING):
            package = next((package for package in packages if package.extension_id == generation.owner), None)
            if package is not None:
                total += self._build(generation, (package, transformers), health)
        return total

    def _build(
        self,
        generation: ProjectionGeneration,
        projection: tuple[
            projector_packages.ProjectorPackage, Sequence[transformer_packages.ProjectionTransformerPackage],
        ],
        health: PassHealth,
    ) -> int:
        """Project one bounded pass into the candidate, then settle it when it failed or has nothing pending.

        Returns:
            The number of facts read.

        """
        recorded = _RecordedHealth(health)
        candidate = replace(
            self.live, record_reader=self.candidate_reader(generation.generation), heads=None,
            generation=generation.generation,
        )
        read = candidate.project_owner(*projection, self.scope_limit, recorded)
        if recorded.failures:
            failed = Diagnostic(code=FAILED_CODE, message=FAILED_MESSAGE).model_dump_json()
            self.generations.settle(generation.generation, GenerationState.FAILED, failed)
        elif not read:
            self.generations.settle(generation.generation, GenerationState.READY)
        return read


@dataclass
class _RecordedHealth(PassHealth):
    """Forward each outcome and remember a failure, so the pass can fail its candidate."""

    health: PassHealth
    failures: int = 0

    def failed(self, extension_id: str) -> None:
        self.failures += 1
        self.health.failed(extension_id)

    def succeeded(self, extension_id: str) -> None:
        self.health.succeeded(extension_id)

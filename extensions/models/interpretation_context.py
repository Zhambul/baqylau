# Copyright (c) 2026 Zhambyl Yermagambet
"""Check pure processing evidence against fixed package and settings selections."""

from collections.abc import Mapping
from dataclasses import dataclass

from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.events import ProcessingContext

from extensions.models.interpretations import InterpretationBinding
from extensions.models.observations import StoredObservation
from extensions.models.processing_package import ProcessingPackage as ProcessingPackage


@dataclass(frozen=True)
class InterpretationContext:
    """Supply host-selected evidence without repositories or worker calls."""

    binding: InterpretationBinding
    original: StoredObservation
    packages: Mapping[str, ProcessingPackage]

    def require_package(self, owner: str) -> ProcessingPackage:
        """Reject a fact or step whose owner is not in the selected active set.

        Returns:
            The exact retained owner declaration and settings.

        Raises:
            ValueError: If the owner is not selected.

        """
        package = self.packages.get(owner)
        if package is None:
            message = "interpretation owner is not enabled in the selected runtime"
            raise ValueError(message)
        return package

    def check_step(self, step: ProcessingContext, capability: str) -> ProcessingPackage:
        """Check the complete context instead of trusting fields returned by a worker.

        Returns:
            The selected provider for this exact pure call.

        Raises:
            ValueError: If the context or capability differs from the selected runtime.

        """
        package = self.require_package(step.extension_id)
        expected = ProcessingContext(
            extension_id=step.extension_id, runtime_revision=self.binding.runtime_revision,
            history_revision=self.binding.history_revision, scope=self.binding.scope,
            input_cursor=self.binding.input_cursor, mode=self.binding.mode,
            settings_revision=package.selection.settings.revision,
            settings=package.selection.settings.for_scope(self.binding.scope),
        )
        if step != expected or capability not in package.manifest.capabilities:
            message = "interpretation step does not match its selected context or capability"
            raise ValueError(message)
        _require_selection_scope(package.manifest, capability, step)
        return package


def _require_selection_scope(manifest: ExtensionManifest, capability: str, step: ProcessingContext) -> None:
    if capability not in {"raw_transformer", "canonical_transformer"}:
        return
    matching = (entry for entry in manifest.contributions.processing if entry.capability == capability)
    selected = next(matching, None)
    if selected is None or step.scope.kind not in selected.scopes:
        message = "transform scope is not in its declared processing selection"
        raise ValueError(message)

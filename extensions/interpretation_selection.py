# Copyright (c) 2026 Zhambyl Yermagambet
"""Capture interpretation identity from a retained runtime and actual storage heads."""

from types import MappingProxyType
from typing import Literal

from baqylau_extension_api.models.events import ProcessingContext

from extensions.models.interpretation_context import InterpretationContext
from extensions.models.interpretation_reads import CanonicalPage
from extensions.models.interpretations import InterpretationBinding
from extensions.models.lifecycle_selection import RuntimePackageSelection
from extensions.models.observations import StoredObservation
from extensions.models.processing_input import observation_scope
from extensions.models.processing_package import ProcessingPackage
from extensions.registry_snapshot import RuntimeSnapshot


def capture_context(
    manager_id: str, snapshot: RuntimeSnapshot, original: StoredObservation, head: CanonicalPage,
    mode: Literal["live", "replay"] = "live",
) -> InterpretationContext:
    """Bind each original to the preceding accepted fact cursor of the head's history.

    Returns:
        A data-only selection in the exact active dependency order.

    """
    binding = InterpretationBinding(
        manager_id=manager_id, runtime_revision=snapshot.directory.runtime_revision,
        history_revision=head.history_revision, raw_event_id=original.observation.raw_event_id,
        input_cursor=original.cursor, scope=observation_scope(original), expected_canonical_cursor=head.head,
        mode=mode,
    )
    related = () if snapshot.relations is None else snapshot.relations.related_scopes(binding.scope)
    return InterpretationContext(binding, original, _packages(snapshot), related)


def processing_context(context: InterpretationContext, owner: str) -> ProcessingContext:
    """Use captured settings, scope, and processing identity for every pure call.

    Returns:
        The exact context checked again at commit.

    """
    package = context.require_package(owner)
    return ProcessingContext(
        extension_id=owner, runtime_revision=context.binding.runtime_revision,
        history_revision=context.binding.history_revision, scope=context.binding.scope,
        input_cursor=context.binding.input_cursor, mode=context.binding.mode,
        settings_revision=package.selection.settings.revision,
        settings=package.selection.settings.for_scope(context.binding.scope, context.related_scopes),
    )


def _packages(snapshot: RuntimeSnapshot) -> MappingProxyType[str, ProcessingPackage]:
    return MappingProxyType({
        owner: ProcessingPackage(RuntimePackageSelection(
            extension_info=package.entry.extension_info, settings=package.settings,
        ), package.manifest, snapshot.schemas)
        for owner in snapshot.active_order for package in snapshot.packages if package.manifest.extension_id == owner
    })

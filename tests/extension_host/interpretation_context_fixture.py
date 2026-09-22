# Copyright (c) 2026 Zhambyl Yermagambet
"""Capture test contexts from retained declarations without encoding original content."""

from pathlib import Path
from types import MappingProxyType

from baqylau_extension_api.schemas import SchemaSet

from extensions.models.interpretation_context import InterpretationContext
from extensions.models.interpretations import InterpretationBinding
from extensions.models.lifecycle_selection import RuntimeSelection
from extensions.models.observations import StoredObservation
from extensions.models.processing_input import observation_scope
from extensions.models.processing_package import ProcessingPackage
from tests.extension_host import catalog_fixture, interpretation_fixture as fixtures


def context(case: fixtures.InterpretationCase, stored: StoredObservation) -> InterpretationContext:
    """Use actual manager, runtime, raw, and canonical state for host preflight tests.

    Returns:
        A data-only context with the exact original row.

    """
    state = case.original.lifecycle.read_extension_lifecycle()
    assert state.manager_id is not None and state.committed_runtime is not None
    binding = InterpretationBinding(
        manager_id=state.manager_id, runtime_revision=state.committed_runtime.runtime_revision,
        history_revision="default", raw_event_id=stored.observation.raw_event_id,
        input_cursor=stored.cursor, scope=observation_scope(stored),
        expected_canonical_cursor=case.store.current_fact_page(0, 1).head,
    )
    return InterpretationContext(binding, stored, _packages(case, state.committed_runtime))


def _packages(
    case: fixtures.InterpretationCase, runtime: RuntimeSelection,
) -> MappingProxyType[str, ProcessingPackage]:
    catalog = catalog_fixture.repository(Path(case.store.database.path).parent).read_extension_catalog()
    return MappingProxyType({
        selected.extension_info.extension_id: ProcessingPackage(
            selected, entry.manifest, SchemaSet(entry.manifest.schemas),
        )
        for selected in runtime.packages for entry in catalog.entries
        if entry.package_digest == selected.extension_info.package_digest and entry.manifest is not None
    })

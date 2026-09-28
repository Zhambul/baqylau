# Copyright (c) 2026 Zhambyl Yermagambet
"""Match scopes and committed facts to one package's declared processing selection."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from baqylau_extension_api.models.canonical import fact_type

if TYPE_CHECKING:
    from collections.abc import Sequence

    from baqylau_extension_api.manifest.data import ProcessingSelection
    from baqylau_extension_api.manifest.observers import ObserverSelection
    from baqylau_extension_api.manifest.package import ExtensionManifest

    from extensions.models.interpretations import StoredCanonicalFact


class FactCapability(StrEnum):
    """Name the capabilities that read committed facts, as the manifest spells them."""

    PROJECTOR = "projector"
    PROJECTION_TRANSFORMER = "projection_transformer"
    OBSERVER = "observer"


def declared_scope_kinds(manifest: ExtensionManifest, capability: FactCapability) -> frozenset[str]:
    """Collect every scope kind that one capability declares.

    Returns:
        The declared scope kinds, empty when the capability selects none.

    """
    return frozenset(
        kind
        for selection in manifest.contributions.processing if selection.capability == capability
        for kind in selection.scopes
    )


def scope_selection(
    manifest: ExtensionManifest, capability: FactCapability, scope_kind: str,
) -> ProcessingSelection | ObserverSelection | None:
    """Select the first declaration for one scope kind, the same rule the SDK checks.

    Returns:
        The declaration, or None when the capability does not select this scope kind.

    """
    return next((
        selection for selection in manifest.contributions.processing
        if selection.capability == capability and scope_kind in selection.scopes
    ), None)


def selected_facts(
    selection: ProcessingSelection | ObserverSelection | None, facts: Sequence[StoredCanonicalFact],
) -> tuple[StoredCanonicalFact, ...]:
    """Keep the facts whose type the declaration selects, in cursor order.

    Returns:
        The selected facts; none when there is no declaration.

    """
    if selection is None:
        return ()
    return tuple(stored for stored in facts if fact_type(stored.fact) in selection.input_types)

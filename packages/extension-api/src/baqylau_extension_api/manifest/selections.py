# Copyright (c) 2026 Zhambyl Yermagambet
"""Check that each fact selection names input types that can exist.

A canonical transformer, a projector, and an observer select committed facts.
A name in a core namespace (such as `shell.`) must be a core event kind, a
name in the package's own namespace must be an event type that it declares,
and a name without a namespace is never a fact type. A misspelled type would
otherwise select nothing and report nothing.

Packages can react to another package's facts without a dependency, so a name
in another namespace is accepted alone. When that package is in the active
set, the name must be one of its declared event types.

Raw and projection-transform selections name source and record kinds that
harnesses and the host define, so they are not checked here.
"""

from collections.abc import Iterator

from baqylau_extension_api.core.registry import CORE_MODELS
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.package import ExtensionManifest

FACT_CAPABILITIES = frozenset(("canonical_transformer", "projector", "observer"))
CORE_NAMESPACES = frozenset(kind.partition(".")[0] for kind in CORE_MODELS)
SEPARATOR = "."


def validate_input_types(manifest: ExtensionManifest) -> None:
    """Refuse a fact selection whose input type cannot exist.

    Raises:
        ExtensionContractError: If an input type is not a core kind, not an owned type, or has no namespace.

    """
    owned = frozenset(definition.name for definition in manifest.contributions.event_types)
    for input_type in _fact_inputs(manifest):
        if input_type not in CORE_MODELS and not _possible(input_type, manifest.extension_id, owned):
            message = f"a selection names an input type that cannot exist: {input_type}"
            raise ExtensionContractError(message)


def validate_peer_input_types(manifest: ExtensionManifest, proposed: tuple[ExtensionManifest, ...]) -> None:
    """Refuse a fact selection of another active package's type that the package does not declare.

    Raises:
        ExtensionContractError: If the active owner does not declare the type.

    """
    for input_type in _fact_inputs(manifest):
        owner = _active_owner(input_type, manifest, proposed)
        declared = () if owner is None else (definition.name for definition in owner.contributions.event_types)
        if owner is not None and input_type not in frozenset(declared):
            message = f"active package {owner.extension_id} does not declare the selected input type {input_type}"
            raise ExtensionContractError(message)


def _possible(input_type: str, owner: str, owned: frozenset[str]) -> bool:
    namespace, separator, _ = input_type.partition(SEPARATOR)
    if not separator or namespace in CORE_NAMESPACES:
        return False
    return input_type in owned if input_type.startswith(f"{owner}{SEPARATOR}") else True


def _active_owner(
    input_type: str, manifest: ExtensionManifest, proposed: tuple[ExtensionManifest, ...],
) -> ExtensionManifest | None:
    others = (peer for peer in proposed if peer.extension_id != manifest.extension_id)
    owners = [peer for peer in others if input_type.startswith(f"{peer.extension_id}{SEPARATOR}")]
    return max(owners, key=lambda peer: len(peer.extension_id), default=None)


def _fact_inputs(manifest: ExtensionManifest) -> Iterator[str]:
    for selection in manifest.contributions.processing:
        if selection.capability in FACT_CAPABILITIES:
            yield from selection.input_types

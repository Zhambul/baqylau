# Copyright (c) 2026 Zhambyl Yermagambet
"""Order a proposed active package set with the standard graph library."""

import heapq
from graphlib import CycleError, TopologicalSorter

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest import peers, rules, selections
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.manifest.validation import require_compatible_api, validate_manifest


def activation_order(manifests: tuple[ExtensionManifest, ...]) -> tuple[str, ...]:
    """Validate a proposed active set before switching any running worker.

    Returns:
        Dependency and explicit order, with extension ID as the final rule.

    """
    checked = tuple(ExtensionManifest.model_validate(manifest) for manifest in manifests)
    rules.require_unique((manifest.extension_id for manifest in checked), "active package IDs")
    sorter: TopologicalSorter[str] = TopologicalSorter()
    for manifest in checked:
        _validate_package(manifest, checked)
        sorter.add(manifest.extension_id, *peers.predecessors(manifest, checked))
    peers.validate_exclusive_views(checked)
    return _ordered(sorter)


def _validate_package(manifest: ExtensionManifest, proposed: tuple[ExtensionManifest, ...]) -> None:
    peer_schemas = tuple(
        schema for peer in proposed if peer.extension_id != manifest.extension_id
        for schema in peer.schemas
    )
    validate_manifest(manifest, peer_schemas)
    require_compatible_api(manifest)
    peers.validate_required_services(manifest, proposed)
    selections.validate_peer_input_types(manifest, proposed)


def _ordered(sorter: TopologicalSorter[str]) -> tuple[str, ...]:
    try:
        sorter.prepare()
    except CycleError as exc:
        message = "extension dependencies or order constraints contain a cycle"
        raise ExtensionContractError(message) from exc
    ready = sorted(sorter.get_ready())
    ordered: list[str] = []
    while ready:
        owner = heapq.heappop(ready)
        ordered.append(owner)
        sorter.done(owner)
        for released in sorter.get_ready():
            heapq.heappush(ready, released)
    return tuple(ordered)

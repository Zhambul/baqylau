# Copyright (c) 2026 Zhambyl Yermagambet
"""Verify explicit SDK protocols before accepting dynamic method entries."""

from baqylau_dev.class_declarations import ClassContract, CodeLocation, class_contracts
from baqylau_dev.manifest_checks import BackendDeclaration
from baqylau_dev.protocol_catalog import ProtocolSpec, sdk_protocols
from baqylau_dev.signatures import satisfies
from baqylau_dev.source_inventory import SourceModule


def verified_protocol_entries(
    backend: BackendDeclaration, modules: tuple[SourceModule, ...],
) -> frozenset[CodeLocation]:
    """Reject undeclared, incomplete, or unsupported protocol implementations.

    Returns:
        Exact dynamic method declarations plus the manifest's factory.

    """
    catalog = sdk_protocols()
    _require_supported_capabilities(backend, catalog)
    entries: set[CodeLocation] = set()
    for contract in class_contracts(modules):
        for protocol in catalog:
            entries.update(_checked_members(contract, protocol, backend.manifest.capabilities))
    entries.add(CodeLocation(
        path=backend.module.path,
        line=backend.factory.decorator_list[0].lineno if backend.factory.decorator_list else backend.factory.lineno,
        name=backend.factory.name,
    ))
    return frozenset(entries)


def _require_supported_capabilities(backend: BackendDeclaration, catalog: tuple[ProtocolSpec, ...]) -> None:
    supported = {protocol.capability for protocol in catalog}
    unsupported = set(backend.manifest.capabilities) - supported
    if unsupported:
        missing = ", ".join(sorted(unsupported))
        message = f"capability protocols are not implemented in this SDK: {missing}"
        raise ValueError(message)


def _checked_members(
    contract: ClassContract, protocol: ProtocolSpec, capabilities: tuple[str, ...],
) -> tuple[CodeLocation, ...]:
    if contract.is_protocol:
        return ()
    matched = satisfies(contract.members, protocol.members)
    declared = protocol.name in contract.bases
    if matched and not declared:
        message = f"{contract.name} implements {protocol.name} without declaring it"
        raise ValueError(message)
    if not declared:
        return ()
    if not matched:
        message = f"{contract.name} does not match the method signatures of {protocol.name}"
        raise ValueError(message)
    if protocol.capability is not None and protocol.capability not in capabilities:
        message = f"{contract.name} implements an undeclared capability: {protocol.capability}"
        raise ValueError(message)
    return tuple(contract.locations[name] for name in protocol.members)

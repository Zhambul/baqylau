# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep process and network I/O where the host can bound it.

An extension runs programs through the host's process service, which checks
the declared programs and their bounds, so its source never imports a process
module. Pure capabilities (transforms, translation, projection, presentation,
and migration) run on the host's engine thread, so a module that declares one
does not import a network module either.
"""

import ast

from baqylau_dev.class_declarations import class_contracts
from baqylau_dev.import_checks import node_imports
from baqylau_dev.protocol_catalog import sdk_protocols
from baqylau_dev.source_inventory import SourceModule

PROCESS_ROOTS = frozenset(("subprocess", "multiprocessing", "pty"))
NETWORK_ROOTS = frozenset(("socket", "ssl", "http", "urllib", "httpx", "requests", "aiohttp"))
PURE_CAPABILITIES = frozenset((
    "raw_transformer", "canonical_transformer", "translator", "projector", "projection_transformer",
    "terminal", "migrations",
))


def check_io(modules: tuple[SourceModule, ...]) -> None:
    """Refuse a process import anywhere, and a network import beside a pure capability; the error names the module."""
    pure = _pure_modules(modules)
    for module in modules:
        roots = _import_roots(module)
        _refuse(module, roots & PROCESS_ROOTS, "; run programs through the host process service")
        if module.name in pure:
            _refuse(module, roots & NETWORK_ROOTS, "; move network work to a live capability")


def _refuse(module: SourceModule, found: frozenset[str], advice: str) -> None:
    if found:
        message = f"{module.name} imports {min(found)}{advice}"
        raise ValueError(message)


def _pure_modules(modules: tuple[SourceModule, ...]) -> frozenset[str]:
    protocols = frozenset(spec.name for spec in sdk_protocols() if spec.capability in PURE_CAPABILITIES)
    contracts = [contract for contract in class_contracts(modules) if contract.bases & protocols]
    return frozenset(contract.name.rpartition(".")[0] for contract in contracts)


def _import_roots(module: SourceModule) -> frozenset[str]:
    nodes = ast.walk(module.tree)
    names = (name for node in nodes for name in node_imports(module, node))
    return frozenset(name.split(".", 1)[0] for name in names)

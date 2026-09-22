# Copyright (c) 2026 Zhambyl Yermagambet
"""Enforce SDK ownership and reuse the existing protocol checks."""

import ast
from pathlib import Path

from baqylau_extension_api.contracts.plugin import ExtensionPlugin

from extensions import contract
from tests import architecture_test_adapters, architecture_test_declarations, architecture_test_protocols
from tests.architecture_test_syntax import ClassDescription, imports_under_path

ROOT = Path(__file__).resolve().parents[2]
SDK_ROOT = ROOT / "packages" / "extension-api" / "src" / "baqylau_extension_api"


def test_sdk_imports_only_its_declared_boundary() -> None:
    """Keep host imports out and confine process execution to runtime adapters."""
    assert SDK_ROOT.is_dir()
    allowed = {
        "__future__", "base64", "binascii", "collections", "dataclasses", "decimal", "hashlib",
        "pathlib", "types", "typing", "graphlib", "heapq", "itertools",
        "pydantic", "packaging", "jsonschema", "referencing", "baqylau_extension_api",
    }
    runtime_only = {
        "argparse", "asyncio", "concurrent", "contextvars", "contextlib", "importlib",
        "jsonrpcpeer", "platform", "socket", "sys", "threading", "time", "uuid",
    }
    violations = [
        f"{path}: {imported}"
        for path in SDK_ROOT.rglob("*.py")
        for _, imported in imports_under_path(path)
        if imported.partition(".")[0] not in _allowed_imports(path, allowed, runtime_only)
    ]
    assert not violations


def _allowed_imports(path: Path, shared: set[str], runtime: set[str]) -> set[str]:
    return shared | runtime if path.is_relative_to(SDK_ROOT / "runtime") else shared


def test_host_contract_contains_only_reexports() -> None:
    """Keep one authoritative public contract in the installed package."""
    tree = ast.parse((ROOT / "extensions" / "contract.py").read_text(encoding="utf-8"))
    for node in tree.body[1:]:
        assert isinstance(node, ast.ImportFrom)
        assert node.module is not None and node.module.startswith("baqylau_extension_api.contracts.")
        assert all(alias.name == alias.asname for alias in node.names)
    assert contract.ExtensionPlugin is ExtensionPlugin


def test_sdk_protocols_are_in_architecture_scan() -> None:
    """Make the shared explicit-Protocol check cover the installed SDK source."""
    protocols, _ = architecture_test_adapters.protocols_and_classes()
    expected = {
        "ExtensionPlugin", "ExtensionLifecycle", "ExtensionRawTransformer", "ExtensionDirectory",
        "ExtensionCanonicalTransformer", "ExtensionTerminalPresenter", "ExtensionQueries", "ExtensionCommands",
        "ExtensionSources", "ExtensionTranslator", "ExtensionProjector", "ExtensionProjectionTransformer",
        "ExtensionMigrations", "ExtensionObserver", "ExtensionServiceAccess",
    }
    assert expected <= protocols.keys()


def test_missing_protocol_base_is_rejected() -> None:
    """Reject a matching method that omits its explicit protocol base."""
    protocols, _ = architecture_test_adapters.protocols_and_classes()
    description = ClassDescription("sample.py:1", "MissingBase", [], {"transform": ("self", "request")})
    assert architecture_test_protocols.undeclared_protocol(description, protocols) is not None


def test_open_json_stays_inside_schema_codecs() -> None:
    """Apply current model rules outside the two schema codec modules."""
    assert SDK_ROOT.is_dir()
    allowed = {
        "packages/extension-api/src/baqylau_extension_api/core/registry.py": {"CORE_MODELS"},
        "packages/extension-api/src/baqylau_extension_api/core/entry_registry.py": {"CORE_ENTRY_MODELS"},
        "packages/extension-api/src/baqylau_extension_api/processing/order.py": {"grouped"},
        "packages/extension-api/src/baqylau_extension_api/processing/canonical.py": {"inputs"},
        "packages/extension-api/src/baqylau_extension_api/processing/raw.py": {"inputs"},
        # This index maps typed record keys to typed rows; it contains no open JSON.
        "packages/extension-api/src/baqylau_extension_api/projection/records.py": {"prior"},
        "packages/extension-api/src/baqylau_extension_api/projection_transform/results.py": {"proposed"},
        "packages/extension-api/src/baqylau_extension_api/projection_transform/state.py": {"actors"},
        "packages/extension-api/src/baqylau_extension_api/runtime/codec.py": {"packet"},
        # This host-only index contains typed grants, not open feature documents.
        "packages/extension-api/src/baqylau_extension_api/runtime/call_grants.py": {"_calls"},
    }
    violations = [
        violation
        for path in SDK_ROOT.rglob("*.py")
        if path.name not in {"schemas.py", "schema_documents.py"}
        for violation in architecture_test_declarations.raw_dictionary_violations(path, allowed)
    ]
    assert not violations

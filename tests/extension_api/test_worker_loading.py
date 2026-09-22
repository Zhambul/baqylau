# Copyright (c) 2026 Zhambyl Yermagambet
"""Check package declarations before imports and verify dynamic factory results."""

import importlib
from types import ModuleType

import pytest
from baqylau_extension_api.contracts.services import ExtensionHostServices
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.runtime import loading

from tests.extension_api import example, samples, worker_samples
from tests.extension_api.test_protocols import SampleDirectory


@pytest.mark.parametrize("change", ["api", "owner", "version", "backend"])
def test_invalid_load_does_not_import(
    monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    """Reject invalid load metadata without running any feature import."""
    request = worker_samples.load_request()
    changes: dict[str, dict[str, object]] = {
        "api": {"api_requires": "==999"},
        "owner": {"extension_id": "test.another"},
        "version": {"package_version": "99"},
        "backend": {"backend": None},
    }
    manifest = request.manifest.model_copy(update=changes[change])
    changed = request.model_copy(update={"manifest": manifest})
    monkeypatch.setattr(importlib, "import_module", _unexpected_import)
    services = ExtensionHostServices(SampleDirectory(), request.environment)
    with pytest.raises(ExtensionContractError):
        loading.load_backend(changed, services)


def _unexpected_import(name: str) -> ModuleType:
    message = f"feature import ran before declaration checks: {name}"
    raise AssertionError(message)


def test_worker_rejects_missing_declared_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not report ready when peer selection omits a declared capability."""
    monkeypatch.setattr(importlib, "import_module", lambda _name: example)
    request = worker_samples.load_request()
    services = ExtensionHostServices(SampleDirectory(), request.environment)
    with pytest.raises(ExtensionContractError, match="handlers do not match"):
        loading.load_backend(request, services)


@pytest.mark.parametrize("factory", [None, "not callable", lambda _services: object()])
def test_worker_rejects_invalid_factory(
    monkeypatch: pytest.MonkeyPatch, factory: object,
) -> None:
    """Keep untyped imports at one checked boundary."""
    module = ModuleType("invalid_external_backend")
    monkeypatch.setattr(module, "build_extension", factory, raising=False)
    monkeypatch.setattr(importlib, "import_module", lambda _name: module)
    request = worker_samples.load_request()
    services = ExtensionHostServices(SampleDirectory(), samples.worker_environment())
    with pytest.raises(ExtensionContractError):
        loading.load_backend(request, services)

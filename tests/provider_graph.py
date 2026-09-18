# Copyright (c) 2026 Zhambyl Yermagambet
"""Resolve the application provider graph for tests."""

from __future__ import annotations

import os
from importlib import import_module
from pkgutil import iter_modules
from typing import cast

import app
from app.injection import Instances, registry, resolve
from tests.provider_graph_application_storage import ProviderGraphApplicationStorage
from tests.provider_graph_event_storage import ProviderGraphEventStorage
from tests.provider_graph_services import ProviderGraphServices

DATA_DIRECTORY_VARIABLES = ("BAQYLAU_DATA_DIR", "BAQYLAU_DATA_DIRECTORY")


class ProviderGraph(ProviderGraphEventStorage, ProviderGraphApplicationStorage, ProviderGraphServices):
    """Provide attribute access to the application provider graph."""

    def __init__(self, instances: Instances | None = None) -> None:
        """Initialize the graph.

        Raises:
            RuntimeError: If no isolated data directory is selected.

        """
        if not any(os.environ.get(name) for name in DATA_DIRECTORY_VARIABLES):
            message = "ProviderGraph needs an isolated data directory; set BAQYLAU_DATA_DIR"
            raise RuntimeError(message)
        self.instances = registry() if instances is None else instances

    def provider[ProviderValue](self, name: str, _expected_type: type[ProviderValue]) -> ProviderValue:
        """Resolve one named provider.

        Returns:
            The provider instance from the test registry.

        Raises:
            AttributeError: If no application provider has the requested name.

        """
        for module_info in iter_modules(app.__path__, "app."):
            if not module_info.name.startswith("app.provider_"):
                continue
            provider = getattr(import_module(module_info.name), name, None)
            if provider is not None and hasattr(provider, "build"):
                return cast("ProviderValue", resolve(self.instances, provider))
        msg = f"no provider named {name!r}"
        raise AttributeError(msg)

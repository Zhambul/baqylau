# Copyright (c) 2026 Zhambyl Yermagambet
"""Give each host-started query its root call grant, so the worker can read a declared peer service."""

from collections.abc import Callable
from dataclasses import dataclass

from baqylau_extension_api.models.scopes import ExtensionScope
from baqylau_extension_api.runtime.call_grants import HostCallLedger

from extensions.registry_package import RegistryPackage


@dataclass(frozen=True)
class QueryAuthority:
    """Open a root grant for the query's package and scope that ends with the call."""

    ledger: HostCallLedger
    seconds: float

    def run[Result](self, package: RegistryPackage, scope: ExtensionScope, call: Callable[[], Result]) -> Result:
        """Run the query inside a grant that ends with the call.

        Returns:
            The call's result.

        """
        if package.environment is None:
            return call()
        with self.ledger.root(package.environment, scope, self.seconds):
            return call()

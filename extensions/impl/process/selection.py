# Copyright (c) 2026 Zhambyl Yermagambet
"""Select only capability proxies verified in the worker ready reply."""

from collections.abc import Callable
from dataclasses import dataclass

from baqylau_extension_api.runtime.contract import RemoteCaller


@dataclass(frozen=True)
class ProxySelection:
    """Bind declared proxy construction to one worker's transport bridge."""

    names: tuple[str, ...]
    caller: RemoteCaller

    def select[Capability](self, name: str, factory: Callable[[RemoteCaller], Capability]) -> Capability | None:
        """Construct the proxy only when the backend actually declares its capability.

        Returns:
            The typed proxy or an absent optional capability.

        """
        return factory(self.caller) if name in self.names else None

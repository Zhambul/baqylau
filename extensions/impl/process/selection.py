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
    # Pure calls on the engine thread use a caller with the shorter transform deadline.
    pure_caller: RemoteCaller | None = None

    def select[Capability](self, name: str, factory: Callable[[RemoteCaller], Capability]) -> Capability | None:
        """Construct the proxy only when the backend actually declares its capability.

        Returns:
            The typed proxy or an absent optional capability.

        """
        return factory(self.caller) if name in self.names else None

    def select_pure[Capability](
        self, name: str, factory: Callable[[RemoteCaller], Capability],
    ) -> Capability | None:
        """Construct a pure engine-thread proxy with the transform deadline.

        Returns:
            The typed proxy or an absent optional capability.

        """
        caller = self.caller if self.pure_caller is None else self.pure_caller
        return factory(caller) if name in self.names else None

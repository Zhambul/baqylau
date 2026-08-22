"""Concrete harness plugin registration — a validated name-to-plugin map."""

from __future__ import annotations

from harness.contract import HarnessPlugin
from domain.events import SCHEMA_VERSION
from domain.errors import UnknownReference
from domain.ids import HarnessName


class HarnessRegistryError(UnknownReference):
    """Raised for a bad REGISTRATION (a duplicate name, a version mismatch) and
    for a bad LOOKUP — and the lookup is the one a request can cause, by naming
    a harness in a URL. As a RuntimeError that reached no handler, an unknown
    harness in `/api/harnesses/{harness}/catalog` was answered as a 500; it is
    the caller's mistake and now says so. A registration failure happens at boot,
    where nothing is serving and the type is irrelevant."""


class HarnessRegistry:
    def __init__(self) -> None:
        self._plugins: dict[HarnessName, HarnessPlugin] = {}

    def register(self, harness_plugin: HarnessPlugin) -> None:
        name = harness_plugin.info.name
        if name in self._plugins:
            raise HarnessRegistryError(f"duplicate harness: {name}")
        if harness_plugin.info.canonical_version != SCHEMA_VERSION:
            raise HarnessRegistryError(
                f"harness {name!r} uses canonical version {harness_plugin.info.canonical_version}, "
                f"expected {SCHEMA_VERSION}"
            )
        if harness_plugin.info.supports_attachments and harness_plugin.launcher is None:
            raise HarnessRegistryError(
                f"harness {name!r} advertises attachments without a launcher"
            )
        if harness_plugin.info.default_for_launch and harness_plugin.launcher is None:
            raise HarnessRegistryError(
                f"harness {name!r} is the launch default but has no launcher"
            )
        if harness_plugin.info.default_for_launch and any(
            registered.info.default_for_launch for registered in self._plugins.values()
        ):
            raise HarnessRegistryError("multiple harnesses are marked as the launch default")
        self._plugins[name] = harness_plugin

    def validate(self) -> None:
        launchable = [plugin for plugin in self._plugins.values() if plugin.launcher is not None]
        defaults = [plugin for plugin in launchable if plugin.info.default_for_launch]
        if launchable and not defaults:
            raise HarnessRegistryError("no launchable harness is marked as the launch default")

    def plugin(self, harness: HarnessName) -> HarnessPlugin:
        try:
            return self._plugins[harness]
        except KeyError as error:
            raise HarnessRegistryError(f"unregistered harness: {harness}") from error

    def plugins(self) -> tuple[HarnessPlugin, ...]:
        return tuple(self._plugins[name] for name in sorted(self._plugins))

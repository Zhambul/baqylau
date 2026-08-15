"""Concrete harness plugin registration — a validated name-to-plugin map."""

from __future__ import annotations

from contracts.harness import HarnessPlugin
from domain.codec import SCHEMA_VERSION


class HarnessRegistryError(RuntimeError):
    pass


class HarnessRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, HarnessPlugin] = {}

    def register(self, plugin: HarnessPlugin) -> None:
        name = plugin.info.name.strip()
        if not name:
            raise HarnessRegistryError("harness name cannot be empty")
        if name != plugin.info.name:
            raise HarnessRegistryError("harness name cannot have surrounding whitespace")
        if name in self._plugins:
            raise HarnessRegistryError(f"duplicate harness: {name}")
        if plugin.info.canonical_version != SCHEMA_VERSION:
            raise HarnessRegistryError(
                f"harness {name!r} uses canonical version {plugin.info.canonical_version}, expected {SCHEMA_VERSION}"
            )
        if plugin.info.supports_attachments and plugin.launcher is None:
            raise HarnessRegistryError(
                f"harness {name!r} advertises attachments without a launcher"
            )
        if plugin.info.default_for_launch and plugin.launcher is None:
            raise HarnessRegistryError(
                f"harness {name!r} is the launch default but has no launcher"
            )
        if plugin.info.default_for_launch and any(
            registered.info.default_for_launch for registered in self._plugins.values()
        ):
            raise HarnessRegistryError("multiple harnesses are marked as the launch default")
        self._plugins[name] = plugin

    def validate(self) -> None:
        launchable = [plugin for plugin in self._plugins.values() if plugin.launcher is not None]
        defaults = [plugin for plugin in launchable if plugin.info.default_for_launch]
        if launchable and not defaults:
            raise HarnessRegistryError("no launchable harness is marked as the launch default")

    def plugin(self, harness: str) -> HarnessPlugin:
        try:
            return self._plugins[harness]
        except KeyError as error:
            raise HarnessRegistryError(f"unregistered harness: {harness}") from error

    def plugins(self) -> tuple[HarnessPlugin, ...]:
        return tuple(self._plugins[name] for name in sorted(self._plugins))

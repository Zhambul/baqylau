# Copyright (c) 2026 Zhambyl Yermagambet
"""Typed startup configuration for installed harnesses."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import starmap
from typing import TYPE_CHECKING

from domain.ids import HarnessName
from harness.impl.definitions import definitions
from harness.models.runtime import HarnessRuntimeConfig as HarnessRuntimeConfig

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping


@dataclass(frozen=True)
class HarnessRuntimeEntry:
    """Represent harness runtime entry."""

    harness: HarnessName
    config: HarnessRuntimeConfig


class HarnessRuntimeConfigs:
    """One runtime configuration, indexed by harness name."""

    def __init__(
        self,
        entries: Iterable[HarnessRuntimeEntry],
    ) -> None:
        """Initialize the object.

        Raises:
            ValueError: If an input value is not valid.

        """
        entry_values = tuple(entries)
        by_harness = {entry.harness: entry.config for entry in entry_values}
        if len(by_harness) != len(entry_values):
            message = "duplicate harness runtime configuration"
            raise ValueError(message)
        self._by_harness: Mapping[HarnessName, HarnessRuntimeConfig] = by_harness

    def for_harness(self, harness: HarnessName) -> HarnessRuntimeConfig:
        """Return the for harness.

        Returns:
            For harness.

        Raises:
            ValueError: If an input value is not valid.

        """
        try:
            return self._by_harness[harness]
        except KeyError as error:
            message = f"missing runtime configuration for {harness}"
            raise ValueError(message) from error

    def entries(self) -> tuple[HarnessRuntimeEntry, ...]:
        """Return the entries.

        Returns:
            Entries.

        """
        return tuple(starmap(HarnessRuntimeEntry, self._by_harness.items()))

    def updated(
        self,
        harness: HarnessName,
        harness_runtime_config: HarnessRuntimeConfig,
    ) -> HarnessRuntimeConfigs:
        """Return the updated.

        Returns:
            Updated.

        """
        return HarnessRuntimeConfigs(
            (
                HarnessRuntimeEntry(
                    name,
                    harness_runtime_config if name == harness else current,
                )
                for name, current in self._by_harness.items()
            ),
        )


def default_harness_runtime_configs() -> HarnessRuntimeConfigs:
    """Read runtime defaults declared by installed plugins.

    Returns:
        The runtime configuration for every installed plugin.

    """
    return HarnessRuntimeConfigs(
        HarnessRuntimeEntry(definition.name, definition.default_runtime_config())
        for definition in definitions()
    )

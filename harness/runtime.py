"""Typed startup configuration for installed harnesses."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from domain.ids import HarnessName


@dataclass(frozen=True)
class HarnessRuntimeConfig:
    executable: str
    configuration_directory: Path
    settings_file: Path | None = None
    use_vendor_default_configuration: bool = False


@dataclass(frozen=True)
class HarnessRuntimeEntry:
    harness: HarnessName
    config: HarnessRuntimeConfig


class HarnessRuntimeConfigs:
    """One runtime configuration, indexed by harness name."""

    def __init__(
        self,
        entries: Iterable[HarnessRuntimeEntry],
    ) -> None:
        entry_values = tuple(entries)
        by_harness = {entry.harness: entry.config for entry in entry_values}
        if len(by_harness) != len(entry_values):
            raise ValueError("duplicate harness runtime configuration")
        self._by_harness: Mapping[HarnessName, HarnessRuntimeConfig] = by_harness

    def for_harness(self, harness: HarnessName) -> HarnessRuntimeConfig:
        try:
            return self._by_harness[harness]
        except KeyError as error:
            raise ValueError(f"missing runtime configuration for {harness}") from error

    def entries(self) -> tuple[HarnessRuntimeEntry, ...]:
        return tuple(
            HarnessRuntimeEntry(harness, config)
            for harness, config in self._by_harness.items()
        )

    def updated(
        self,
        harness: HarnessName,
        harness_runtime_config: HarnessRuntimeConfig,
    ) -> HarnessRuntimeConfigs:
        return HarnessRuntimeConfigs(
            (
                HarnessRuntimeEntry(
                    name,
                    harness_runtime_config if name == harness else current,
                )
                for name, current in self._by_harness.items()
            )
        )


def _installed_executable(candidates: tuple[str, ...], fallback: str) -> str:
    return next(
        (
            candidate
            for candidate in candidates
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK)
        ),
        fallback,
    )


def default_harness_runtime_configs() -> HarnessRuntimeConfigs:
    home = Path.home()
    native_codex_candidates = tuple(
        str(candidate)
        for candidate in sorted(
            (
                home
                / ".hermes"
                / "node"
                / "lib"
                / "node_modules"
                / "@openai"
                / "codex"
                / "node_modules"
                / "@openai"
            ).glob("codex-*/vendor/*/bin/codex")
        )
    )
    return HarnessRuntimeConfigs(
        (
            HarnessRuntimeEntry(
                HarnessName.CLAUDE_CODE,
                HarnessRuntimeConfig(
                    _installed_executable(
                        (
                            str(home / ".local" / "bin" / "claude"),
                            "/opt/homebrew/bin/claude",
                            "/usr/local/bin/claude",
                        ),
                        "claude",
                    ),
                    home / ".claude",
                    use_vendor_default_configuration=True,
                ),
            ),
            HarnessRuntimeEntry(
                HarnessName.CODEX,
                HarnessRuntimeConfig(
                    _installed_executable(
                        (
                            *native_codex_candidates,
                            str(home / ".hermes" / "node" / "bin" / "codex"),
                            "/opt/homebrew/bin/codex",
                            "/usr/local/bin/codex",
                            str(home / ".local" / "bin" / "codex"),
                        ),
                        "codex",
                    ),
                    home / ".codex",
                ),
            ),
        )
    )

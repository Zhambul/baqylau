# Copyright (c) 2026 Zhambyl Yermagambet
"""Declare a plugin without building its services."""

from collections.abc import Callable
from dataclasses import dataclass

from domain.ids import HarnessName
from harness.models.runtime import HarnessRuntimeConfig


@dataclass(frozen=True)
class HarnessDefinition:
    """Provide the name and runtime defaults owned by a plugin."""

    name: HarnessName
    default_runtime_config: Callable[[], HarnessRuntimeConfig]

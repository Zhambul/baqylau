# Copyright (c) 2026 Zhambyl Yermagambet
"""Run one extension profile in a private daemon and collect its measurements."""

from __future__ import annotations

import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING

from tests.extension_host import (
    lifecycle_daemon_fixture as fixture,
    lifecycle_http_fixture as lifecycle,
    process_fixture,
)
from tests.perf import benchmark_idle, benchmark_probes as probes

if TYPE_CHECKING:
    from sdk.client import BaqylauClient

# Each behavior is a lifecycle fixture package: `keep` keeps every fact, `rawreplace` also
# rewrites raw input, and `slow` waits 3 seconds in each canonical call.
PROFILES = MappingProxyType({
    "none": (),
    "one": ("keep",),
    "two": ("keep", "rawreplace"),
    "slow-worker": ("slow",),
})
HIGH_PERCENTILE = 0.95
type Names = tuple[str, ...]


@dataclass
class ProfileResult:
    """Keep one profile's measurements."""

    profile: str
    extensions: tuple[str, ...]
    events: int
    throughput_per_second: float = 0
    delay_median_ms: float = 0
    delay_p95_ms: float = 0
    max_pending: int = 0
    memory_mb: float = 0
    idle_cpu_seconds: float = 0
    idle_context_switches: int = 0
    health: list[str] = field(default_factory=list)


def measure(profile: str, hooks: tuple[bytes, ...], wheels: Path) -> ProfileResult:
    """Install the profile's packages, start a daemon, and measure it.

    Returns:
        The measurements.

    """
    result = ProfileResult(profile, PROFILES[profile], len(hooks))
    with tempfile.TemporaryDirectory(prefix=f"baqylau-perf-{profile}-") as temporary:
        owners = install(Path(temporary), wheels, result.extensions)
        with process_fixture.running_catalog(Path(temporary)) as client:
            for owner in owners:
                enable(client, owner)
            record(client, hooks, result)
    return result


def record(client: BaqylauClient, hooks: tuple[bytes, ...], result: ProfileResult) -> None:
    """Send the capture, then single hooks, then wait while idle, and keep each measurement."""
    started = time.monotonic()
    for hook in hooks:
        probes.post(client, hook)
    result.max_pending = probes.drain(client, len(hooks))
    elapsed = time.monotonic() - started
    result.throughput_per_second = len(hooks) / elapsed
    ordered = probes.delays(client, len(hooks))
    result.delay_median_ms = ordered[len(ordered) // 2]
    result.delay_p95_ms = percentile(ordered, HIGH_PERCENTILE)
    result.memory_mb = probes.memory_mb(client)
    usage = benchmark_idle.idle(client)
    result.idle_cpu_seconds = usage.cpu_seconds
    result.idle_context_switches = usage.context_switches
    result.health = [entry.model_dump_json() for entry in client.extensions.lifecycle.health().extensions]


def percentile(ordered: list[float], share: float) -> float:
    """Read one percentile of ordered values.

    Returns:
        The value.

    """
    index = max(0, int(len(ordered) * share) - 1)
    return ordered[index]


def install(directory: Path, wheels: Path, behaviors: Names) -> Names:
    """Install each lifecycle fixture package into the daemon's package root.

    Returns:
        The owners.

    """
    (directory / "packages").mkdir()
    owners = []
    for behavior in behaviors:
        stage = directory / f"stage-{behavior}"
        staged = fixture.installed(stage, wheels, behavior)
        target = directory / "packages" / staged.owner
        staged.package.rename(target)
        owners.append(staged.owner)
    return tuple(owners)


def enable(client: BaqylauClient, owner: str) -> None:
    """Enable one package and wait for its operation.

    Raises:
        RuntimeError: If the package does not enable.

    """
    request = lifecycle.lifecycle_request(client, owner, "enable", f"enable-{owner}")
    admitted = client.extensions.lifecycle.change(owner, request)
    status = lifecycle.wait_operation(client, admitted.operation.operation_id).status
    if status != "succeeded":
        message = f"{owner} did not enable: {status}"
        raise RuntimeError(message)

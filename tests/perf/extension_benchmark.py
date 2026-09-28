# Copyright (c) 2026 Zhambyl Yermagambet
"""Measure throughput, delay, queue depth, memory, and idle cost with zero, one, and two extensions (P08-T04).

    python -m tests.perf.extension_benchmark [--events N] [--large] [--profiles ...] [--output FILE]

Each profile starts a private daemon, enables its packages, and receives the same
fixed capture of native Claude Code hooks. The report names the capture digest,
the machine, and the runtime versions.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import platform
import sys
import tempfile
from pathlib import Path

from tests.extension_host import wheel_fixture
from tests.perf import benchmark_capture as captures, benchmark_profiles as profiles

DEFAULT_EVENTS = 300
type Result = dict[str, object]


def options(arguments: list[str]) -> argparse.Namespace:
    """Read the command options.

    Returns:
        The options.

    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=int, default=DEFAULT_EVENTS)
    parser.add_argument("--large", action="store_true", help="send 256 KiB assistant messages")
    parser.add_argument("--profiles", nargs="*", default=list(profiles.PROFILES))
    parser.add_argument("--output", type=Path)
    return parser.parse_args(arguments)


def environment(hooks: tuple[bytes, ...]) -> dict[str, object]:
    """Describe the capture, the machine, and the runtime versions.

    Returns:
        The description.

    """
    return {
        "capture_sha256": captures.digest(hooks),
        "capture_events": len(hooks),
        "machine": platform.platform(),
        "cpu_count": os.cpu_count(),
        "python": platform.python_version(),
        "call_seconds": os.environ.get("BAQYLAU_EXTENSION_CALL_SECONDS", "30 (default)"),
    }


def measured(selected: argparse.Namespace, hooks: tuple[bytes, ...]) -> list[Result]:
    """Measure each selected profile with one offline wheelhouse.

    Returns:
        The results.

    """
    with tempfile.TemporaryDirectory(prefix="baqylau-perf-wheels-") as directory:
        wheels = wheel_fixture.build_wheels(Path(directory))
        return [dataclasses.asdict(profiles.measure(profile, hooks, wheels)) for profile in selected.profiles]


def message_bytes(selected: argparse.Namespace) -> int:
    """Choose the assistant message size of the capture.

    Returns:
        The bytes.

    """
    return captures.LARGE_MESSAGE_BYTES if selected.large else captures.SMALL_MESSAGE_BYTES


def main(arguments: list[str]) -> int:
    """Run the command.

    Returns:
        The exit code.

    """
    selected = options(arguments)
    hooks = captures.capture(selected.events, message_bytes(selected))
    results_list = measured(selected, hooks)
    report = {"environment": environment(hooks), "large": selected.large, "results": results_list}
    text = json.dumps(report, indent=2)
    if selected.output is not None:
        selected.output.write_text(f"{text}\n", encoding="utf-8")
    sys.stdout.write(f"{text}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

# Copyright (c) 2026 Zhambyl Yermagambet
"""Filter Vulture results by exact verified declarations, not global names."""

import sys
from pathlib import Path

from pydantic import BaseModel
from vulture import Vulture  # type: ignore[import-untyped]  # Vulture 2.16 has no typing marker.

from baqylau_dev.class_declarations import CodeLocation
from baqylau_dev.commands import DEADCODE_DECORATORS, absolute_roots
from baqylau_dev.models import ProjectProfile


class DeadCodeFinding(BaseModel):
    """Read the installed tool's finding through a typed data boundary."""

    filename: Path
    first_lineno: int
    name: str
    message: str
    confidence: int


def check_deadcode(root: Path, profile: ProjectProfile, entries: frozenset[CodeLocation]) -> int:
    """Keep Vulture's scan and framework policy with exact dynamic exceptions.

    Returns:
        Nonzero for unused product code or an invalid Vulture input.

    """
    scanner = Vulture(ignore_decorators=DEADCODE_DECORATORS.split(","))
    scanner.scavenge(absolute_roots(root, profile.source_roots))
    failures = 0
    for raw in scanner.get_unused_code():
        finding = DeadCodeFinding.model_validate(raw, from_attributes=True)
        location = CodeLocation(
            path=finding.filename.resolve(), line=finding.first_lineno, name=finding.name,
        )
        if location not in entries:
            sys.stdout.write(
                f"{finding.filename}:{finding.first_lineno}: {finding.message} ({finding.confidence}% confidence)\n",
            )
            failures += 1
    return int(scanner.exit_code) or int(bool(failures))

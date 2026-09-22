# Copyright (c) 2026 Zhambyl Yermagambet
"""Limit runtime locks to pinned wheel requirements, without path or installer directives."""

import re
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement

from extensions.discovery_bytes import read_document

MAX_REQUIREMENTS_BYTES = 1_048_576
HASH_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")


def validate_runtime_requirements(path: Path) -> None:
    """Check the accepted standard requirements subset before the installer runs.

    Package names, extras, versions, and markers use the packaging library parser.
    Hash continuations and comments are accepted. Paths, URLs, nested requirements,
    editable source, and installer options are not runtime package inputs.

    """
    encoded = read_document(path, MAX_REQUIREMENTS_BYTES)
    document = re.sub(r"\\\r?\n", "", encoded.decode("utf-8"))
    for line in document.splitlines():
        selected = line.partition("#")[0].strip()
        if selected:
            _validate_requirement(selected)


def _validate_requirement(line: str) -> None:
    parts = re.split(r"\s+--hash=", line)
    requirement = _parse_requirement(parts[0])
    _validate_hashes([part.strip() for part in parts[1:]])
    versions = tuple(requirement.specifier)
    if requirement.url is not None or len(versions) != 1:
        message = "runtime requirements need one exact version and no direct source"
        raise ValueError(message)
    if versions[0].operator != "==" or "*" in versions[0].version:
        message = "runtime requirements must pin one exact version"
        raise ValueError(message)


def _parse_requirement(line: str) -> Requirement:
    try:
        return Requirement(line)
    except InvalidRequirement:
        message = "runtime requirements must use pinned package names, not installer directives"
        raise ValueError(message) from None


def _validate_hashes(hashes: list[str]) -> None:
    valid_hashes = all(HASH_PATTERN.fullmatch(digest) is not None for digest in hashes)
    if not hashes or not valid_hashes:
        message = "runtime requirements need complete SHA-256 wheel hashes"
        raise ValueError(message)

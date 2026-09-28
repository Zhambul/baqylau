# Copyright (c) 2026 Zhambyl Yermagambet
"""Check that a package's declared E2E cases cover its features, surfaces, harnesses, and peers.

A web view needs a `web` case and a terminal view needs a `kitty` case; a unit
test is not a declared case. A case that does not test every harness that the
host discovered needs an explicit harness limit with a reason, and a limit on a
case that tests every harness is stale. Each declared dependency needs a case
that names it as a peer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.manifest.validation import validate_manifest

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from baqylau_extension_api.manifest.e2e import E2eCase

MANIFEST_NAME = "extension.json"


def read_manifest(package: Path) -> ExtensionManifest:
    """Read and check the package's manifest without running its code.

    Returns:
        The checked manifest.

    """
    return validate_manifest(ExtensionManifest.model_validate_json((package / MANIFEST_NAME).read_bytes()))


def coverage_findings(package: Path, manifest: ExtensionManifest, harnesses: frozenset[str]) -> tuple[str, ...]:
    """Find every missing or invalid coverage declaration.

    Returns:
        One line for each finding; none when the declarations are complete.

    """
    return (
        *_missing_files(package, manifest),
        *_missing_surfaces(manifest),
        *(finding for case in manifest.e2e for finding in _harness_findings(case, harnesses)),
        *_missing_peers(manifest),
    )


def _missing_files(package: Path, manifest: ExtensionManifest) -> Iterator[str]:
    for case in manifest.e2e:
        if not (package / case.path).is_file():
            yield f"case {case.case_id}: {case.path} is not a file in the package"


def _missing_surfaces(manifest: ExtensionManifest) -> Iterator[str]:
    covered = {surface for case in manifest.e2e for surface in case.surfaces}
    features = (
        ("web", bool(manifest.contributions.web), "a web view"),
        ("kitty", bool(manifest.contributions.terminal), "a terminal view"),
        ("worker", manifest.backend is not None, "a backend"),
    )
    for surface, declared, feature in features:
        if declared and surface not in covered:
            yield f"the package declares {feature} but no case covers the {surface} surface"


def _harness_findings(case: E2eCase, harnesses: frozenset[str]) -> Iterator[str]:
    tested = frozenset(case.harnesses)
    unknown = ", ".join(sorted(tested - harnesses))
    if unknown:
        yield f"case {case.case_id}: the host does not discover {unknown}"
    if tested == harnesses and case.harness_limit is not None:
        yield f"case {case.case_id}: the harness limit is stale; the case tests every harness"
    if tested != harnesses and case.harness_limit is None:
        missing = ", ".join(sorted(harnesses - tested))
        yield f"case {case.case_id}: it does not test {missing} and needs a harness limit with a reason"


def _missing_peers(manifest: ExtensionManifest) -> Iterator[str]:
    covered = {peer for case in manifest.e2e for peer in case.peers}
    for dependency in manifest.dependencies:
        if dependency.extension_id not in covered:
            yield f"no case names the dependency {dependency.extension_id} as a peer"

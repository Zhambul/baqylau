# Copyright (c) 2026 Zhambyl Yermagambet
"""Discover built external packages without starting a worker or importing code."""

from collections import Counter
from pathlib import Path

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.validation import require_compatible_api
from pydantic import ValidationError

from extensions import discovery_manifest
from extensions.configuration import ExtensionRoots
from extensions.discovery_contract import ExtensionPackageScanner
from extensions.models.catalog import DiscoveryIssue, PackageCandidate, PackageScan, RootDiscoveryIssue

MAX_PACKAGE_ENTRIES = 1000


class FilesystemExtensionScanner(ExtensionPackageScanner):
    """Inspect each configured root and keep every invalid package visible."""

    def scan_packages(self, roots: ExtensionRoots) -> PackageScan:
        """Read a complete bounded catalog with explicit root failures.

        Returns:
            Candidate metadata, never active runtime state.

        """
        entries: list[PackageCandidate] = []
        failures: list[RootDiscoveryIssue] = []
        directories = sorted({path.expanduser().absolute() for path in roots.directories})
        for directory in directories:
            try:
                entries.extend(_inspect_package(path) for path in _package_paths(directory))
            except (OSError, ValueError):
                failures.append(RootDiscoveryIssue(
                    root_path=str(directory), issue=DiscoveryIssue(
                        code="root_unavailable", detail="The package root could not be read within its limits.",
                    ),
                ))
        return PackageScan(entries=_reject_duplicates(tuple(entries)), root_issues=tuple(failures))


def _package_paths(directory: Path) -> tuple[Path, ...]:
    if not directory.exists() and not directory.is_symlink():
        return ()
    paths = []
    for count, path in enumerate(directory.iterdir(), 1):
        if count > MAX_PACKAGE_ENTRIES:
            message = "package root exceeds its entry limit"
            raise ValueError(message)
        if path.is_dir() or path.is_symlink():
            paths.append(path)
    return tuple(sorted(paths))


def _inspect_package(path: Path) -> PackageCandidate:
    try:
        return _load_candidate(path)
    except OSError:
        return PackageCandidate(source_path=str(path), issue=DiscoveryIssue(
            code="unreadable", detail="The package could not be read.",
        ))
    except (ValueError, ExtensionContractError, ValidationError):
        return PackageCandidate(source_path=str(path), issue=DiscoveryIssue(
            code="invalid_manifest", detail="The package manifest is absent, invalid, or too large.",
        ))


def _load_candidate(path: Path) -> PackageCandidate:
    directory = path.resolve(strict=True)
    manifest, encoded = discovery_manifest.read_manifest(directory)
    candidate = PackageCandidate(
        source_path=str(path), resolved_path=str(directory), manifest=manifest,
        issue=DiscoveryIssue(code="invalid_files", detail="The package files could not be validated."),
    )
    try:
        require_compatible_api(manifest)
    except ExtensionContractError:
        return candidate.with_issue(DiscoveryIssue(
            code="incompatible_api", detail="The package does not support this host API version.",
        ))
    try:
        digest = discovery_manifest.checked_package_digest(directory, manifest, encoded)
    except (ValueError, OSError):
        return candidate.with_issue(DiscoveryIssue(
            code="invalid_files", detail="Package files are absent, changed, linked, or outside their limits.",
        ))
    return candidate.with_digest(digest)


def _reject_duplicates(entries: tuple[PackageCandidate, ...]) -> tuple[PackageCandidate, ...]:
    counts = Counter(entry.manifest.extension_id for entry in entries if entry.manifest is not None)
    return tuple(
        entry.with_issue(DiscoveryIssue(
            code="duplicate_id", detail="More than one package declares this extension ID.",
        )) if entry.manifest is not None and counts[entry.manifest.extension_id] > 1 else entry
        for entry in sorted(entries, key=lambda entry: entry.source_path)
    )

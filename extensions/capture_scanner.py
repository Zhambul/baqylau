# Copyright (c) 2026 Zhambyl Yermagambet
"""Capture valid discovery entries before the application publishes their catalog."""

from dataclasses import dataclass

from baqylau_extension_api.errors import ExtensionContractError

from extensions.artifact_contract import ExtensionArtifacts
from extensions.configuration import ExtensionRoots
from extensions.discovery_contract import ExtensionPackageScanner
from extensions.models.artifacts import PackageCaptureRequest
from extensions.models.catalog import DiscoveryIssue, PackageCandidate, PackageScan


@dataclass(frozen=True)
class CapturingExtensionScanner(ExtensionPackageScanner):
    """Keep file capture outside the catalog transaction and separate from activation."""

    scanner: ExtensionPackageScanner
    artifacts: ExtensionArtifacts

    def scan_packages(self, roots: ExtensionRoots) -> PackageScan:
        """Publish valid metadata only when its fixed file copy can be checked.

        Returns:
            The captured catalog, or typed discovery and capture failures.

        """
        scanned = self.scanner.scan_packages(roots)
        if scanned.root_issues:
            return scanned
        return PackageScan(entries=tuple(self._capture(entry) for entry in scanned.entries))

    def _capture(self, package_candidate: PackageCandidate) -> PackageCandidate:
        if package_candidate.issue is not None:
            return package_candidate
        try:
            self.artifacts.capture_package(_capture_request(package_candidate))
        except (OSError, ValueError, ExtensionContractError):
            return package_candidate.with_issue(DiscoveryIssue(
                code="capture_failed", detail="The checked package copy could not be prepared. Rescan after repair.",
            ))
        return package_candidate


def _capture_request(package_candidate: PackageCandidate) -> PackageCaptureRequest:
    source = package_candidate.resolved_path
    digest = package_candidate.package_digest
    manifest = package_candidate.manifest
    if source is None or digest is None or manifest is None:
        message = "package capture requires complete discovery metadata"
        raise ValueError(message)
    return PackageCaptureRequest(source_path=source, expected_digest=digest, expected_manifest=manifest)

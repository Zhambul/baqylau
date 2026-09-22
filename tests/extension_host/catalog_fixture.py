# Copyright (c) 2026 Zhambyl Yermagambet
"""Compose catalog fixtures with private databases and explicit package roots."""

from pathlib import Path

from fastapi.testclient import TestClient

from api.app import build_web_application
from app import injection, provider_databases, provider_extension_artifacts, provider_extensions
from extensions.artifacts import FilesystemExtensionArtifacts
from extensions.catalog import ExtensionCatalogService
from extensions.configuration import ExtensionRoots
from extensions.discovery import FilesystemExtensionScanner
from extensions.discovery_contract import ExtensionPackageScanner
from extensions.models.catalog import PackageScan
from repository.impl.sqlite import databases
from repository.impl.sqlite.extension_catalog import SqliteExtensionCatalogRepository


class RecordedScanner(ExtensionPackageScanner):
    """Count actual file scans without changing discovery behavior."""

    def __init__(self) -> None:
        """Start with no recorded file scans."""
        self.calls = 0

    def scan_packages(self, roots: ExtensionRoots) -> PackageScan:
        """Count and perform the real scanner call.

        Returns:
            The same typed scan as the production scanner.

        """
        self.calls += 1
        return FilesystemExtensionScanner().scan_packages(roots)


def repository(directory: Path) -> SqliteExtensionCatalogRepository:
    """Use a new database object for the same private catalog path.

    Returns:
        The real SQLite repository.

    """
    return SqliteExtensionCatalogRepository(databases.main_database(str(directory / "main.db")))


def service(directory: Path, scanner: ExtensionPackageScanner | None = None) -> ExtensionCatalogService:
    """Build a service without using application environment defaults.

    Returns:
        A catalog backed by real package files and private SQLite storage.

    """
    roots = ExtensionRoots((directory / "packages",))
    return ExtensionCatalogService(roots, scanner or FilesystemExtensionScanner(), repository(directory))


def web_client(directory: Path) -> TestClient:
    """Seed both application databases before any server lifespan work.

    Returns:
        A context-managed client for the real application routes.

    """
    instances = injection.registry()
    main = databases.main_database(str(directory / "main.db"))
    audit = databases.audit_database(str(directory / "audit.db"))
    injection.seed(instances, provider_databases.main_db, main)
    injection.seed(instances, provider_databases.audit_db, audit)
    injection.seed(instances, provider_extensions.extension_roots, ExtensionRoots((directory / "packages",)))
    artifact_store: provider_extension_artifacts.Artifacts = FilesystemExtensionArtifacts(
        directory / "extension-artifacts",
    )
    injection.seed(instances, provider_extension_artifacts.extension_artifacts, artifact_store)
    return TestClient(build_web_application(instances))

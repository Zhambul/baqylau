# Copyright (c) 2026 Zhambyl Yermagambet
"""Compose the application-owned extension discovery catalog."""

import os
from pathlib import Path
from typing import Annotated

from fastapi import Depends

from app.injection import singleton
from app.provider_databases import MainDb
from app.provider_extension_artifacts import Scanner
from extensions.catalog import ExtensionCatalogService
from extensions.configuration import ExtensionRoots, configured_roots
from extensions.discovery_contract import ExtensionCatalog
from repository.impl.sqlite.extension_catalog import SqliteExtensionCatalogRepository


@singleton
def extension_roots(database: MainDb) -> ExtensionRoots:
    """Read the explicit runtime roots or the application-local default.

    Returns:
        Package roots for this application instance.

    """
    return configured_roots(os.environ, Path(database.path).parent)


Roots = Annotated[ExtensionRoots, Depends(extension_roots)]


@singleton
def extension_catalog(roots: Roots, database: MainDb, scanner: Scanner) -> ExtensionCatalog:
    """Build discovery with the application's own database and configuration.

    Returns:
        A typed catalog service which does not start package workers.

    """
    return ExtensionCatalogService(roots, scanner, SqliteExtensionCatalogRepository(database))


Catalog = Annotated[ExtensionCatalog, Depends(extension_catalog)]

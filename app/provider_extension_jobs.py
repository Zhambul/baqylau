# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide the durable extension job repository over the main database."""

from typing import Annotated

from fastapi import Depends

from app import provider_databases as database_providers
from app.injection import singleton
from repository.contract.extension_jobs import ExtensionJobRepository
from repository.impl.sqlite.extension_jobs import SqliteExtensionJobRepository


@singleton
def extension_jobs(database: database_providers.MainDb) -> ExtensionJobRepository:
    """Return the extension job repository.

    Returns:
        The job repository over the main database.

    """
    return SqliteExtensionJobRepository(database)


Jobs = Annotated[
    ExtensionJobRepository,
    Depends(extension_jobs),
]

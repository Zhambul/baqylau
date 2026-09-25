# Copyright (c) 2026 Zhambyl Yermagambet
"""Read the retained lifecycle operation history for the management page."""

from typing import Annotated

from fastapi import Depends

from app.injection import singleton
from app.provider_databases import MainDb
from repository.impl.sqlite.extension_operation_history import SqliteOperationHistory


@singleton
def operation_history(database: MainDb) -> SqliteOperationHistory:
    """Read stored operations without the daemon's extension manager.

    Returns:
        The history reader.

    """
    return SqliteOperationHistory(database)


OperationHistory = Annotated[SqliteOperationHistory, Depends(operation_history)]

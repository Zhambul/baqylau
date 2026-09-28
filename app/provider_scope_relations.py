# Copyright (c) 2026 Zhambyl Yermagambet
"""Share one scope relation reader between settings resolution and web views."""

from typing import Annotated

from fastapi import Depends

from app.injection import singleton
from app.provider_databases import MainDb
from extensions.models.scope_relations import ScopeRelations
from repository.impl.sqlite.scope_relations import SqliteScopeRelations


@singleton
def scope_relations(database: MainDb) -> ScopeRelations:
    """Relate a session to its workspace through the stored session rows.

    Returns:
        The relations that settings resolution and web views share.

    """
    return SqliteScopeRelations(database)


Relations = Annotated[ScopeRelations, Depends(scope_relations)]

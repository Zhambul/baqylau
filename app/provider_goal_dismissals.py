# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide goal-dismissal storage."""

from typing import Annotated

from fastapi import Depends

from app.injection import singleton
from app.provider_databases import MainDb
from repository.contract.goal_dismissals import GoalDismissalRepository
from repository.impl.sqlite.goal_dismissals import SqliteGoalDismissalRepository


@singleton
def goal_dismissals(database: MainDb) -> GoalDismissalRepository:
    """Return goal-dismissal storage.

    Returns:
        Goal-dismissal storage.

    """
    return SqliteGoalDismissalRepository(database)


GoalDismissals = Annotated[GoalDismissalRepository, Depends(goal_dismissals)]

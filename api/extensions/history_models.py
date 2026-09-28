# Copyright (c) 2026 Zhambyl Yermagambet
"""Typed candidate history reads."""

from pydantic import BaseModel

from repository.contract.history_reprocessing import HistoryComparison, ReprocessingState


class HistoryReprocessingResponse(BaseModel):
    """Describe one candidate history of a closed session, its state, and its comparison."""

    history_revision: str
    session_id: str
    state: ReprocessingState
    comparison: HistoryComparison | None = None
    diagnostic: str | None = None

# Copyright (c) 2026 Zhambyl Yermagambet
"""Start, read, and request the switch of one closed session's candidate history."""

from http import HTTPStatus

from fastapi import APIRouter, Depends, HTTPException

from api.common.models.fields import SessionIdPath
from api.extensions.admission import require_catalog_write
from api.extensions.history_models import HistoryReprocessingResponse
from app.provider_reprocessing import Histories
from domain.ids import SessionId
from repository.contract.history_reprocessing import (
    HistoryReprocessing,
    ReprocessingRefusedError,
    ReprocessingState,
)

router = APIRouter()
CANDIDATE_PATH = "/api/history/candidates/{history_revision}"
SWITCHABLE = (ReprocessingState.READY, ReprocessingState.RETIRED)


@router.post(
    "/api/history/sessions/{session_id}/reprocess",
    dependencies=[Depends(require_catalog_write)],
    status_code=HTTPStatus.ACCEPTED,
)
def reprocess_session(session_id: SessionIdPath, histories: Histories) -> HistoryReprocessingResponse:
    """Start replaying one finished session into a candidate history; the engine builds it.

    Returns:
        The new building candidate.

    Raises:
        HTTPException: If the session is outside the safe V1 boundary.

    """
    try:
        return history_response(histories.create(SessionId(session_id)))
    except ReprocessingRefusedError as error:
        raise HTTPException(HTTPStatus.CONFLICT, str(error)) from error


@router.get(CANDIDATE_PATH)
def candidate_history(history_revision: str, histories: Histories) -> HistoryReprocessingResponse:
    """Read one candidate history.

    Returns:
        The candidate, its state, and its comparison.

    """
    return history_response(_candidate(history_revision, histories))


@router.post(
    f"{CANDIDATE_PATH}/activate", dependencies=[Depends(require_catalog_write)], status_code=HTTPStatus.ACCEPTED,
)
def activate_candidate_history(history_revision: str, histories: Histories) -> HistoryReprocessingResponse:
    """Request the switch of a ready or retired history; the engine switches it after its next drain.

    Returns:
        The candidate with the switch requested.

    Raises:
        HTTPException: If the candidate is not ready or retired.

    """
    candidate = _candidate(history_revision, histories)
    if candidate.state not in SWITCHABLE:
        raise HTTPException(HTTPStatus.CONFLICT, "only a ready or retired history can become live")
    histories.settle(history_revision, ReprocessingState.SWITCHING)
    return history_response(_candidate(history_revision, histories))


def history_response(history_reprocessing: HistoryReprocessing) -> HistoryReprocessingResponse:
    """Map one stored candidate onto the typed response.

    Returns:
        The typed candidate response.

    """
    return HistoryReprocessingResponse(
        history_revision=history_reprocessing.history_revision,
        session_id=history_reprocessing.session_id,
        state=history_reprocessing.state,
        comparison=history_reprocessing.comparison,
        diagnostic=history_reprocessing.diagnostic,
    )


def _candidate(history_revision: str, histories: Histories) -> HistoryReprocessing:
    stored = histories.read(history_revision)
    if stored is None:
        raise HTTPException(HTTPStatus.NOT_FOUND, "candidate history not found")
    return stored

"""Resumable sessions to the picker's model."""

from __future__ import annotations

from api.common.mapper import values
from api.dashboard.models.resume.resumable_session_response import ResumableSessionResponse
from app.services.resume import ResumableSession


def resumable_session(session: ResumableSession) -> ResumableSessionResponse:
    return ResumableSessionResponse(
        session_id=session.session_id,
        title=session.title,
        last_activity_at=session.last_activity_at,
        active=session.active,
        harness=session.harness,
        model=values.maybe_model_reference(session.model),
        effort=session.effort,
        account=values.maybe_account_reference(session.account),
    )

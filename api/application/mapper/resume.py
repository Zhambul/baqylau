"""Resumable sessions to the picker's model."""

from __future__ import annotations

from api.common.mapper import values
from api.application.models.resume.resumable_session_response import ResumableSessionResponse
from app.services.resume import ResumableSession


def resumable_session(resumable_session: ResumableSession) -> ResumableSessionResponse:
    return ResumableSessionResponse(
        session_id=resumable_session.session_id,
        title=resumable_session.title,
        last_activity_at=resumable_session.last_activity_at,
        active=resumable_session.active,
        harness=resumable_session.harness,
        model=values.maybe_model_reference(resumable_session.model),
        effort=resumable_session.effort,
        account=values.maybe_account_reference(resumable_session.account),
    )

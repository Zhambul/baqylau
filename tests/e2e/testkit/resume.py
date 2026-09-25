# Copyright (c) 2026 Zhambyl Yermagambet
"""Prepare and resolve one real native session resume."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from api.application.models.resume.resumable_session_response import (
    ResumableSessionResponse,
)
from sdk.client import BaqylauClient, SessionRef, wait_for
from tests.e2e.testkit import selector_turns
from tests.e2e.testkit.references import (
    SessionContinuationRef,
    SessionSpec,
    TurnRef,
)

if TYPE_CHECKING:
    from sdk.state import SessionSnapshot
    from tests.e2e.testkit.policy import WaitPolicy


@dataclass(frozen=True)
class ResumePreparation:
    """Represent resume preparation."""

    source: SessionRef
    source_cursor: int
    saved: ResumableSessionResponse
    spec: SessionSpec


@dataclass(frozen=True)
class ResumeCompletion:
    """Represent resume completion."""

    continuation: SessionContinuationRef
    turn: TurnRef


class SessionResumeSupport:
    """Keep resume discovery and prompt ownership independent of its origin."""

    def __init__(self, client: BaqylauClient, wait_policy: WaitPolicy) -> None:
        """Initialize the object."""
        self._client = client
        self._wait_policy = wait_policy

    def prepare(self, source: SessionRef) -> ResumePreparation:
        """Wait for a session's saved resume metadata.

        Returns:
            The saved session, current cursor, and launch configuration.

        Raises:
            AssertionError: If the saved session has no model.

        """
        before = self._client.sessions.snapshot(source)
        workspace = before.session_data.session.working_directory
        saved = wait_for(
            f"session {source.session_id!r} to enter the resume list",
            lambda: self._saved_session(source, workspace),
            timeout=self._wait_policy.feed,
        )
        if saved.model is None:
            message = f"saved session {source.session_id!r} has no model"
            raise AssertionError(message)
        return ResumePreparation(
            source=source,
            source_cursor=before.entry_cursor,
            saved=saved,
            spec=SessionSpec(
                harness=saved.harness,
                model=saved.model.name,
                effort=saved.effort or "",
                workspace=workspace,
                account_id=(None if saved.account is None else saved.account.account_id),
            ),
        )

    def complete(self, prepared: ResumePreparation, prompt: str) -> ResumeCompletion:
        """Find the session that owns the resumed prompt and observe its turn.

        Returns:
            The session continuation and its observed turn.

        """
        owner = self._client.sessions.wait_for_prompt_owner(
            prepared.source,
            prompt=prompt,
            after_cursor=prepared.source_cursor,
            timeout=self._wait_policy.session_announcement,
        )
        owner_snapshot = self._client.sessions.snapshot(owner)
        owner_lead = owner_snapshot.lead()
        cursor_before = prepared.source_cursor if owner == prepared.source else 0
        turn = selector_turns.turn(
            self._client.sessions.watch(owner),
            TurnRef(
                owner,
                prompt,
                cursor_before,
                owner_lead.statistics.prompt_count,
                actor_id=owner_lead.actor_id,
            ),
            self._wait_policy.feed,
        )
        return ResumeCompletion(
            continuation=SessionContinuationRef(
                prepared.source,
                owner,
                prepared.saved,
            ),
            turn=turn,
        )

    def _saved_session(
        self,
        source: SessionRef,
        workspace: str,
    ) -> ResumableSessionResponse | None:
        matches = tuple(
            resume_candidate
            for resume_candidate in self._client.insights.resumable_sessions(workspace=workspace)
            if resume_candidate.session_id == source.session_id
        )
        if len(matches) > 1:
            message = f"resume list has {len(matches)} rows for session {source.session_id!r}"
            raise AssertionError(
                message,
            )
        return matches[0] if matches else None


def _saved_resume(continuation: SessionContinuationRef) -> ResumableSessionResponse:
    saved = continuation.saved
    if saved is None:
        session_id = continuation.before.session_id
        message = f"session {session_id!r} has no saved resume row"
        raise AssertionError(
            message,
        )
    return saved


def _assert_saved_account(snapshot: SessionSnapshot, saved: ResumableSessionResponse) -> None:
    actual_account = snapshot.session_data.session.account
    expected_account = saved.account
    if expected_account is None:
        assert actual_account is None
    else:
        assert actual_account is not None
        assert actual_account.account_id == expected_account.account_id
        assert actual_account.display_name == expected_account.display_name


def assert_saved_metadata(
    client: BaqylauClient,
    continuation: SessionContinuationRef,
) -> None:
    """Process assert saved metadata."""
    saved = _saved_resume(continuation)
    snapshot = client.sessions.snapshot(continuation.after)
    lead = snapshot.lead()
    expected_model = (
        None if saved.model is None
        else saved.model.display_name or saved.model.name
    )
    assert saved.active is False
    assert snapshot.session_data.session.harness == saved.harness
    assert snapshot.session_data.session.title == saved.title
    assert lead.model == expected_model
    if saved.effort is not None:
        assert lead.effort == saved.effort
    _assert_saved_account(snapshot, saved)


def assert_one_live_session(
    client: BaqylauClient,
    continuation: SessionContinuationRef,
) -> None:
    """Process assert one live session."""
    before = client.sessions.snapshot(continuation.before)
    after = client.sessions.snapshot(continuation.after)
    if continuation.before != continuation.after:
        assert after.session_data.session.continued_from == continuation.before.session_id
        assert not before.session_data.live
    assert after.session_data.live
    assert (
        sum(
            {
                continuation.before.session_id: before.session_data.live,
                continuation.after.session_id: after.session_data.live,
            }.values(),
        )
        == 1
    )

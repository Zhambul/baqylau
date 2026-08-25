"""Prepare and resolve one real native session resume."""

from __future__ import annotations

from dataclasses import dataclass

from api.application.models.resume.resumable_session_response import (
    ResumableSessionResponse,
)
from sdk.client import BaqylauClient, SessionRef, wait_for
from tests.e2e.testkit import selectors
from tests.e2e.testkit.policy import WaitPolicy
from tests.e2e.testkit.references import (
    SessionContinuationRef,
    SessionSpec,
    TurnRef,
)


@dataclass(frozen=True)
class ResumePreparation:
    source: SessionRef
    source_cursor: int
    saved: ResumableSessionResponse
    spec: SessionSpec


@dataclass(frozen=True)
class ResumeCompletion:
    continuation: SessionContinuationRef
    turn: TurnRef


class SessionResumeSupport:
    """Keep resume discovery and prompt ownership independent of its origin."""

    def __init__(self, client: BaqylauClient, wait_policy: WaitPolicy) -> None:
        self._client = client
        self._wait_policy = wait_policy

    def prepare(self, source: SessionRef) -> ResumePreparation:
        before = self._client.sessions.snapshot(source)
        workspace = before.data.session.working_directory
        saved = wait_for(
            f"session {source.session_id!r} to enter the resume list",
            lambda: self._saved_session(source, workspace),
            timeout=self._wait_policy.feed,
        )
        if saved.model is None:
            raise AssertionError(f"saved session {source.session_id!r} has no model")
        if saved.effort is None:
            raise AssertionError(f"saved session {source.session_id!r} has no effort")
        return ResumePreparation(
            source=source,
            source_cursor=before.cursor,
            saved=saved,
            spec=SessionSpec(
                harness=saved.harness,
                model=saved.model.name,
                effort=saved.effort,
                workspace=workspace,
                account_id=(
                    saved.account.account_id if saved.account is not None else None
                ),
            ),
        )

    def complete(self, prepared: ResumePreparation, prompt: str) -> ResumeCompletion:
        owner = self._client.sessions.wait_for_prompt_owner(
            prepared.source,
            prompt=prompt,
            after_cursor=prepared.source_cursor,
            timeout=self._wait_policy.session_announcement,
        )
        owner_snapshot = self._client.sessions.snapshot(owner)
        owner_lead = owner_snapshot.lead()
        cursor_before = prepared.source_cursor if owner == prepared.source else 0
        turn = selectors.turn(
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
            item
            for item in self._client.insights.resumable_sessions(workspace=workspace)
            if item.session_id == source.session_id
        )
        if len(matches) > 1:
            raise AssertionError(
                f"resume list has {len(matches)} rows for session "
                f"{source.session_id!r}"
            )
        return matches[0] if matches else None


def assert_saved_metadata(
    client: BaqylauClient,
    continuation: SessionContinuationRef,
) -> None:
    saved = continuation.saved
    if saved is None:
        raise AssertionError(
            f"session {continuation.before.session_id!r} has no saved resume row"
        )
    snapshot = client.sessions.snapshot(continuation.after)
    lead = snapshot.lead()
    expected_model = (
        saved.model.display_name or saved.model.name
        if saved.model is not None
        else None
    )
    actual_account = snapshot.data.session.account
    expected_account = saved.account
    assert saved.active is False
    assert snapshot.data.session.harness == saved.harness
    assert snapshot.data.session.title == saved.title
    assert lead.model == expected_model
    assert lead.effort == saved.effort
    if expected_account is None:
        assert actual_account is None
    else:
        assert actual_account is not None
        assert actual_account.account_id == expected_account.account_id
        assert actual_account.display_name == expected_account.display_name


def assert_one_live_session(
    client: BaqylauClient,
    continuation: SessionContinuationRef,
) -> None:
    before = client.sessions.snapshot(continuation.before)
    after = client.sessions.snapshot(continuation.after)
    if continuation.before != continuation.after:
        assert after.data.session.continued_from == continuation.before.session_id
        assert not before.data.live
    assert after.data.live
    assert sum({
        continuation.before.session_id: before.data.live,
        continuation.after.session_id: after.data.live,
    }.values()) == 1

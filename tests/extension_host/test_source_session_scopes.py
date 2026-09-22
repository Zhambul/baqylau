# Copyright (c) 2026 Zhambyl Yermagambet
"""Select complete active actor scopes from the core aggregate read protocol."""

from unittest.mock import Mock

from baqylau_extension_api.models.scopes import InstallationScope, SessionScope

from domain import actor_state, ids, lifecycle, messaging, session_state
from extensions.source_scopes import ActiveExtensionScopes, SessionSourceScopes
from repository.contract.session_data_protocols import SessionDataAggregateRead

SESSION = ids.SessionId("session")
HARNESS = ids.HarnessName("test")
LEAD = ids.ActorId("lead")
CHILD = ids.ActorId("child")


def test_every_running_actor_has_a_source_scope() -> None:
    """The lead and child have distinct scopes; a finished actor has no active source."""
    sessions = Mock(spec=SessionDataAggregateRead, running=Mock(return_value=(_session(),)))
    scopes = SessionSourceScopes(sessions, ActiveExtensionScopes(Mock()))
    assert set(scopes.source_scopes()) == {
        InstallationScope(), SessionScope(session_id=SESSION, actor_id=LEAD, harness=HARNESS),
        SessionScope(session_id=SESSION, actor_id=CHILD, harness=HARNESS),
    }


def test_finished_sessions_remove_actor_scopes() -> None:
    """A source pass reads current core state instead of keeping finished actor identities."""
    sessions = Mock(spec=SessionDataAggregateRead, running=Mock(return_value=(_session(),)))
    scopes = SessionSourceScopes(sessions, ActiveExtensionScopes(Mock()))
    assert SessionScope(session_id=SESSION, actor_id=LEAD, harness=HARNESS) in scopes.source_scopes()
    sessions.running.return_value = ()
    assert scopes.source_scopes() == (InstallationScope(),)


def _session() -> session_state.SessionData:
    return session_state.SessionData(session=session_state.SessionFacts(
        session_id=SESSION, harness=HARNESS, state=lifecycle.LifecycleState.RUNNING,
        working_directory="/project", started_at=1000, lead_actor_id=LEAD,
    ), actors=(
        _actor(LEAD, messaging.ActorRole.LEAD), _actor(CHILD, messaging.ActorRole.CHILD),
        _actor(ids.ActorId("finished"), messaging.ActorRole.CHILD, lifecycle.LifecycleState.FINISHED),
    ), cursor=1)


def _actor(
    actor_id: ids.ActorId, role: messaging.ActorRole,
    state: lifecycle.LifecycleState = lifecycle.LifecycleState.RUNNING,
) -> actor_state.ActorFacts:
    return actor_state.ActorFacts(
        session_id=SESSION, actor_id=actor_id, role=role, name=str(actor_id), state=state,
    )

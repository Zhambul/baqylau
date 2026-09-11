# Copyright (c) 2026 Zhambyl Yermagambet
"""Shared values for canonical session data API tests."""

from __future__ import annotations

import domain as domain_modules
from domain import (
    actor_state,
    ids as domain_ids,
    lifecycle,
    messaging,
    session_state,
)
from tests.harness_names import CLAUDE_CODE_HARNESS

SESSION_ID_TEXT = "session-one"
WORKING_DIRECTORY = "/work/baqylau"
SESSION_TITLE = "Fix the SSE reconnect bug"
MODEL_DISPLAY_NAME = "Fable 5"
MESSAGE_ID_TEXT = "m1"
SHELL_ID_TEXT = "s"
ATTENTION_ID_TEXT = "a"
PROMPT_TEXT = "go"
ACTORS_FIELD = "actors"
ENTRIES_FIELD = "entries"
SESSION = domain_ids.SessionId(SESSION_ID_TEXT)
LEAD = domain_ids.ActorId("session-one:lead")
SESSION_START_TIME = 1_755_590_000.0
ENTRY_TIME = 1_755_590_100.0
ENTRY_CURSOR = 4_810
SNAPSHOT_CURSOR = 4_812
LIVE_SESSION_CURSOR = 12
PARKED_SESSION_CURSOR = 11
SESSION_LIST_CURSOR = 15
FINISHED_SESSION_CURSOR = 16
ACTOR_INPUT_TOKENS = 12_000
ACTOR_OUTPUT_TOKENS = 4_100
ACTOR_CONTEXT_USED_TOKENS = 61_000
ACTOR_CONTEXT_WINDOW_TOKENS = 200_000
SHELL_COMMAND_COUNT = 12
LINES_ADDED = 120
LINES_REMOVED = 30
ACTOR_ACTIVE_SECONDS = 1_240.0
OPEN_INTERVAL_READ_TIME = 1_030.0
OPEN_INTERVAL_TOTAL_SECONDS = 130.0
COMPACTION_RESULT_TOKENS = 4_000
LATEST_ACTIVITY_TIME = 1_755_599_999.0
MAXIMUM_TITLE_CHARACTERS = 80
NOTIFICATION_SETTLE_SECONDS = 30.0
NO_UPDATE_WAIT_SECONDS = 0.8


# One of each, and `dataclasses.replace` for the differences. A dict of defaults
# updated with kwargs is the same builder untyped: every field arrives as
# `object`, so a Literal spelled wrong would reach the mapper unremarked — and
# what these tests check IS the mapping.
FACTS = session_state.SessionFacts(
    session_id=SESSION,
    harness=CLAUDE_CODE_HARNESS,
    state=lifecycle.LifecycleState.RUNNING,
    working_directory=WORKING_DIRECTORY,
    started_at=SESSION_START_TIME,
    lead_actor_id=LEAD,
    title=SESSION_TITLE,
    account=domain_modules.references.AccountReference(domain_ids.AccountId("acc_01"), "zhambyl"),
)
ACTOR = actor_state.ActorFacts(
    session_id=SESSION,
    actor_id=LEAD,
    role=messaging.ActorRole.LEAD,
    name="Claude",
    state=lifecycle.LifecycleState.RUNNING,
    status=actor_state.ActorStatus.EXECUTING,
    model=domain_modules.references.ModelReference("claude-fable-5", MODEL_DISPLAY_NAME),
    effort="high",
)
ACTOR_ROWS = (ACTOR,)


AN_ENTRY_ID = domain_ids.CanonicalEventId("event-one")

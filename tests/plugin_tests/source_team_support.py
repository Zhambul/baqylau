# Copyright (c) 2026 Zhambyl Yermagambet
"""Support for team message source tests."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from domain import (
    ids as domain_ids,
)
from harness.impl.claude_code.canonical.sources import (
    ClaudeTranscriptRawEventSource,
)
from harness.models.session import (
    Session,
)
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_runtime import interpreting_runtime

if TYPE_CHECKING:
    from pathlib import Path

    from harness.models import raw_events as raw_event_models


@dataclass(frozen=True)
class TeamMessageCase:
    """Describe source context and expected ownership of a team message."""

    source_actor_id: str
    source_parent_actor_id: domain_ids.ActorId | None
    sender: str
    expected_actor_id: str
    expected_parent_actor_id: domain_ids.ActorId | None
    starts_actor: bool
    expected_role: str


def claude_team_message_audit(
    tmp_path: Path,
    message_case: TeamMessageCase,
) -> raw_event_models.RawEventAudit:
    """Write and interpret a transcript containing one team message.

    Returns:
        The last raw-event audit for the test session.

    """
    source_path = tmp_path / "transcript.jsonl"
    source_path.write_text(
        "\n".join(
            (
                json.dumps(
                    {
                        fixture.TYPE_FIELD: fixture.USER,
                        fixture.UUID_FIELD: "session-prompt",
                        fixture.MESSAGE_FIELD: {fixture.CONTENT_FIELD: "begin"},
                    },
                ),
                json.dumps(
                    {
                        fixture.TYPE_FIELD: fixture.USER,
                        fixture.UUID_FIELD: "team-message-one",
                        fixture.MESSAGE_FIELD: {
                            fixture.CONTENT_FIELD: (
                                f'<teammate-message teammate_id="{message_case.sender}">hello</teammate-message>'
                            ),
                        },
                    },
                ),
                "",
            ),
        ),
    )
    session = Session(
        domain_ids.SessionId(fixture.SESSION_ONE_ID),
        domain_ids.ActorId(fixture.SESSION_ONE_LEAD_ID),
        source_path.as_posix(),
        fixture.WORK_PATH,
    )
    runtime, interpreter = interpreting_runtime(tmp_path / fixture.DATA_FIELD / fixture.MAIN_DB_PATH)
    runtime.register(CLAUDE_CODE_HARNESS, session)
    context = replace(
        session.source_context,
        actor_id=domain_ids.ActorId(message_case.source_actor_id),
        parent_actor_id=message_case.source_parent_actor_id,
    )
    runtime.recorder.record(ClaudeTranscriptRawEventSource(context).read(None))
    interpreter.tick()
    return runtime.raw_event_audits.audits_for_session(session.session_id)[-1]

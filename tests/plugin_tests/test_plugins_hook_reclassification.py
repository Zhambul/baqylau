# Copyright (c) 2026 Zhambyl Yermagambet
"""A hook that its agent metadata reclassifies is recorded again."""

import json
from pathlib import Path

import pytest

from domain import ids as domain_ids
from harness.impl.claude_code.hooks import gateway as claude_hooks
from tests.canonical_runtime import CanonicalRuntime
from tests.plugin_tests import support_hooks, vocabulary as fixture


def _subagent_start(transcript: Path) -> bytes:
    return json.dumps(
        {
            fixture.SESSION_ID_FIELD: fixture.SESSION_ONE_ID,
            fixture.TRANSCRIPT_PATH: str(transcript),
            fixture.HOOK_EVENT_NAME_FIELD: fixture.SUBAGENT_START_HOOK,
            fixture.AGENT_ID_FIELD: fixture.WORKER_ONE_ID,
        },
    ).encode()


def _mark_teammate(transcript: Path) -> None:
    metadata = transcript.with_suffix("") / fixture.SUBAGENTS / f"agent-{fixture.WORKER_ONE_ID}.meta.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(json.dumps({"taskKind": "in_process_teammate"}))


def test_reclassified_hook_is_a_new_observation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Record one payload again when agent metadata corrects its source type."""
    monkeypatch.setenv(fixture.BAQYLAU_DATA_DIR_ENV, tmp_path.as_posix())
    transcript = tmp_path / fixture.SESSION_ONE_JSONL_PATH
    payload = _subagent_start(transcript)
    gateway = claude_hooks.ClaudeHookGateway()

    # The meta sidecar lags the first delivery, so it reads as an ordinary child.
    support_hooks.deliver_hook(gateway, payload)
    _mark_teammate(transcript)
    support_hooks.deliver_hook(gateway, payload)

    evidence = CanonicalRuntime(str(tmp_path / fixture.MAIN_DB_PATH)).raw_event_audits.audits_for_session(
        domain_ids.SessionId(fixture.SESSION_ONE_ID),
    )
    assert [str(row.raw_event.source_type) for row in evidence] == ["hook", "teammate_hook"]

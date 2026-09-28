# Copyright (c) 2026 Zhambyl Yermagambet
"""A Codex session that continues in a later history page is read there too."""

import json
from pathlib import Path

from domain import ids as domain_ids
from harness.impl.codex.canonical.source_readers import CodexRolloutRawEventSource
from harness.impl.codex.canonical.sources import CodexRawEventSources
from harness.models.session import Session
from tests.plugin_tests import source_codex_native_support, vocabulary as fixture

SESSION_ID = "01a0dbf0-53f0-7cd3-9c04-d6c517eb26e2"
LEAD_NAME = f"rollout-2026-09-26T12-19-15-{SESSION_ID}.jsonl"
PAGE_NAME = f"rollout-2026-09-26T12-19-21-{SESSION_ID}_01a0dbf0-6b31-77f0.jsonl"


def _header() -> str:
    payload = {"id": SESSION_ID}
    return json.dumps({fixture.TYPE_FIELD: fixture.SESSION_META_ID, fixture.PAYLOAD_FIELD: payload})


def _session(tmp_path: Path) -> Session:
    directory = source_codex_native_support.native_session_directory(tmp_path)
    directory.mkdir(parents=True)
    header = _header()
    for name in (LEAD_NAME, PAGE_NAME):
        (directory / name).write_text(f"{header}\n", encoding=fixture.TEXT_ENCODING)
    lead_actor = domain_ids.ActorId(f"{SESSION_ID}:lead")
    lead_path = str(directory / LEAD_NAME)
    return Session(domain_ids.SessionId(SESSION_ID), lead_actor, lead_path, str(tmp_path))


def _rollout_readers(tmp_path: Path, session: Session) -> list[CodexRolloutRawEventSource]:
    sources = CodexRawEventSources(str(tmp_path)).for_session(session)
    return [source for source in sources if isinstance(source, CodexRolloutRawEventSource)]


def test_later_history_page_is_a_lead_source(tmp_path: Path) -> None:
    """The page after a rewind is read with the first rollout, for the lead."""
    session = _session(tmp_path)
    readers = _rollout_readers(tmp_path, session)
    assert [Path(reader.source_path).name for reader in readers] == [LEAD_NAME, PAGE_NAME]
    assert {reader.context.actor_id for reader in readers} == {session.lead_actor_id}

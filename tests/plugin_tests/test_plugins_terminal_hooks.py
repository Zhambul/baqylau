# Copyright (c) 2026 Zhambyl Yermagambet
"""Terminal hook gateway process tests."""

from __future__ import annotations

import json
from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING

import pytest

from domain.ids import SessionId, WindowId
from repository.impl.sqlite import databases, raw_events
from tests.canonical_runtime import CanonicalRuntime
from tests.harness_names import CLAUDE_CODE_HARNESS, CODEX_HARNESS
from tests.plugin_tests import support_hooks, terminal_hook_dependencies as hook_dependencies, vocabulary as fixture

if TYPE_CHECKING:
    from pathlib import Path

type AncestryCall = tuple[str, int | None]


def _record_ancestry_call(
    ancestry_calls: list[AncestryCall],
    process_name: str,
    from_process_id: int | None = None,
) -> int:
    ancestry_calls.append((process_name, from_process_id))
    return fixture.FIXTURE_PROCESS_ID


def test_hook_gateway_service_records_only(tmp_path: Path) -> None:
    """Verify hook gateway service records only for harnesses that accept deliveries."""
    registry = hook_dependencies.HarnessRegistry()
    for plugin in hook_dependencies.installed():
        registry.register(
            replace(plugin, hooks=None) if plugin.harness_info.name == fixture.CODEX_HARNESS else plugin,
        )
    service = hook_dependencies.HookGatewayService(
        registry, raw_events.SqliteRawEventRepository(databases.main_database(str(tmp_path / fixture.MAIN_DB_PATH))),
    )
    payload = json.dumps(
        {
            fixture.SESSION_ID_FIELD: fixture.SESSION_ONE_ID,
            fixture.TRANSCRIPT_PATH: fixture.WORK_SESSION_JSONL_PATH,
            fixture.HOOK_EVENT_NAME_FIELD: fixture.POST_TOOL_USE_HOOK,
            fixture.HOOK_EVENT_ID_FIELD: "post-one",
            fixture.TOOL_NAME_FIELD: fixture.READ_TOOL,
        },
    ).encode()

    assert not service.record(
        CLAUDE_CODE_HARNESS,
        support_hooks.hook_request(payload, terminal_window_id=WindowId("9")),
    )
    evidence = CanonicalRuntime(str(tmp_path / fixture.MAIN_DB_PATH)).raw_event_audits.audits_for_session(
        SessionId(fixture.SESSION_ONE_ID),
    )
    assert [audit.raw_event.source_type for audit in evidence] == [fixture.HOOK_SOURCE]

    with pytest.raises(hook_dependencies.UnknownHookHarnessError, match="accepts no hook deliveries"):
        service.record(CODEX_HARNESS, support_hooks.hook_request(payload))


def test_cli_pid_is_resolved_from_pid_its_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A client observes; the daemon interprets.

    A hook process used to walk its own ancestry with `ps` — up to 32 forks in a
    process the harness is waiting on — to name the CLI, which took the harness's
    own process name and so an import of its plugin. It reports its own pid
    instead, and the walk happens here, where the process name is already known
    and where the chain is still alive: the CLI is blocked on this delivery.
    """
    ancestry_calls: list[AncestryCall] = []

    monkeypatch.setattr(
        "harness.hooks.gateway.nearest_ancestor_named",
        partial(_record_ancestry_call, ancestry_calls),
    )
    registry = hook_dependencies.HarnessRegistry()
    for plugin in hook_dependencies.installed():
        registry.register(plugin)
    service = hook_dependencies.HookGatewayService(
        registry, raw_events.SqliteRawEventRepository(databases.main_database(str(tmp_path / fixture.MAIN_DB_PATH))),
    )
    payload = json.dumps(
        {
            fixture.SESSION_ID_FIELD: fixture.SESSION_ONE_ID,
            fixture.TRANSCRIPT_PATH: fixture.WORK_SESSION_JSONL_PATH,
            fixture.HOOK_EVENT_NAME_FIELD: fixture.SESSION_START_HOOK,
            fixture.HOOK_EVENT_ID_FIELD: "start-one",
        },
    ).encode()

    service.record(
        CLAUDE_CODE_HARNESS, support_hooks.hook_request(payload, client_process_id=fixture.CLIENT_PROCESS_ID),
    )

    assert ancestry_calls == [(fixture.CLAUDE, fixture.CLIENT_PROCESS_ID)]
    assert [
        audit.raw_event.harness_process_id
        for audit in CanonicalRuntime(
            str(tmp_path / fixture.MAIN_DB_PATH),
        ).raw_event_audits.audits_for_session(
            SessionId(fixture.SESSION_ONE_ID),
        )
        if audit.raw_event.source_type == fixture.HOOK_SOURCE
    ] == [fixture.FIXTURE_PROCESS_ID]

    # Nothing to walk from: a delivery with no client pid claims no CLI pid.
    ancestry_calls.clear()
    service.record(CLAUDE_CODE_HARNESS, support_hooks.hook_request(payload))
    assert ancestry_calls == []

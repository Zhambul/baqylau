# Copyright (c) 2026 Zhambyl Yermagambet
"""Codex shell input audit storage tests."""

import json
from pathlib import Path

from domain.event_shell import ShellInputProvided
from domain.ids import ActorId, RawEventId, SessionId
from harness.models.raw_events import RawEventAudit
from harness.models.session import Session
from tests.harness_names import CODEX_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import raw_event
from tests.plugin_tests.support_runtime import interpreting_runtime


def test_codex_write_stdin_records_raw_and_canon(tmp_path: Path) -> None:
    """Verify codex write stdin records raw and canonical audit."""
    runtime, interpreter = interpreting_runtime(tmp_path / fixture.MAIN_DB_PATH)
    runtime.register(
        CODEX_HARNESS,
        Session(
            SessionId(fixture.SESSION_ONE_ID),
            ActorId(fixture.SESSION_ONE_LEAD_ID),
            "fixture.jsonl",
            fixture.WORK_PATH,
        ),
    )
    observations = (
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: fixture.COMMAND_ONE,
                    fixture.INPUT_FIELD: 'tools.exec_command({"cmd":"read value"})',
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id=fixture.COMMAND_FIELD,
            source_position=fixture.FORTY_TEXT,
        ),
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_OUTPUT_ID,
                    fixture.CALL_ID_FIELD: fixture.COMMAND_ONE,
                    fixture.OUTPUT_FIELD: json.dumps({
                        fixture.SESSION_ID_FIELD: 77,
                        fixture.OUTPUT_FIELD: "",
                    }),
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id=fixture.COMMAND_OUTPUT_ID,
            source_position=fixture.FORTY_ONE_TEXT,
        ),
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: "poll-one",
                    fixture.INPUT_FIELD: 'tools.write_stdin({session_id:77,chars:""})',
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="poll",
            source_position="42",
        ),
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: fixture.INPUT_ONE_ID,
                    fixture.INPUT_FIELD: r'tools.write_stdin({session_id:77,chars:"yes\n"})',
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id=fixture.STDIN,
            source_position="43",
        ),
    )
    runtime.recorder.record(observations)
    interpreter.tick()

    stdin_evidence = runtime.raw_event_audits.audit(RawEventId(fixture.STDIN))
    _assert_translated_stdin_evidence(stdin_evidence, observations[-1].payload)
    poll_evidence = runtime.raw_event_audits.audit(RawEventId("poll"))
    _assert_ignored_poll_evidence(poll_evidence, observations[-2].payload)


def _assert_translated_stdin_evidence(
    evidence: RawEventAudit | None,
    expected_payload: bytes,
) -> None:
    """Verify the translated write-stdin evidence.

    Raises:
        AssertionError: If the audit or its interpretation is missing.

    """
    if evidence is None:
        msg = "write-stdin evidence is missing"
        raise AssertionError(msg)
    interpretation = evidence.interpretation
    if interpretation is None:
        msg = "write-stdin evidence has no interpretation"
        raise AssertionError(msg)
    assert evidence.raw_event.payload == expected_payload
    assert interpretation.decision == fixture.TRANSLATED
    assert isinstance(next(iter(interpretation.events)).event.payload, ShellInputProvided)


def _assert_ignored_poll_evidence(
    evidence: RawEventAudit | None,
    expected_payload: bytes,
) -> None:
    """Verify the ignored polling evidence.

    Raises:
        AssertionError: If the audit or its interpretation is missing.

    """
    if evidence is None:
        msg = "poll evidence is missing"
        raise AssertionError(msg)
    interpretation = evidence.interpretation
    if interpretation is None:
        msg = "poll evidence has no interpretation"
        raise AssertionError(msg)
    assert evidence.raw_event.payload == expected_payload
    assert interpretation.decision == fixture.IGNORED_NONSEMANTIC
    assert interpretation.events == ()

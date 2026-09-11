# Copyright (c) 2026 Zhambyl Yermagambet
"""Build the native start sequence for control effect tests."""

from __future__ import annotations

from dataclasses import replace

from domain import ids as domain_ids
from engine.interpret import translators as interpret_translators
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from tests import (
    control_effect_native_model as native_model,
    control_effect_native_payload as native_payload,
    control_effect_resume as control_resume,
)
from tests.harness_names import CLAUDE_CODE_HARNESS


def native_start_sequence() -> native_model.NativeStartSequence:
    """Translate resume, native start, and exit records for related runs.

    Returns:
        The translation results for the resume and native lifecycle records.

    """
    raw_events = control_resume.resumed_raw_events(
        CLAUDE_CODE_HARNESS,
        "/transcripts/session-one.jsonl",
        domain_ids.WindowId("window-two"),
    )
    native_raw_event = replace(
        raw_events.events[0],
        raw_event_id=domain_ids.RawEventId("native-start-two"),
        source_type="hook",
        source_name="SessionStart",
        source_position="native-start-two",
        payload=native_payload.hook_payload("SessionStart", "native-start-two"),
    )
    return native_model.NativeStartSequence(
        interpret_translators.SessionResumeTranslator().translate(raw_events.events[0]),
        ClaudeCanonicalTranslator().translate(native_raw_event),
        ClaudeCanonicalTranslator().translate(
            replace(
                native_raw_event,
                raw_event_id=domain_ids.RawEventId("native-start-three"),
                source_position="native-start-three",
                terminal_window_id=domain_ids.WindowId("window-three"),
            ),
        ),
        ClaudeCanonicalTranslator().translate(
            replace(
                native_raw_event,
                raw_event_id=domain_ids.RawEventId("native-end-two"),
                source_name="SessionEnd",
                source_position="native-end-two",
                payload=native_payload.hook_payload("SessionEnd", "native-end-two"),
            ),
        ),
        interpret_translators.ResumeLivenessTranslator().translate(
            replace(
                raw_events.events[0],
                raw_event_id=domain_ids.RawEventId("resume-liveness-two"),
                source_type="resume_liveness",
                source_position="window-two:closed",
                payload=b"",
            ),
        ),
    )

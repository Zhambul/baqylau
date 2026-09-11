# Copyright (c) 2026 Zhambyl Yermagambet
"""Claude telemetry storage tests."""

from pathlib import Path

from domain.ids import ActorId, SessionId
from harness.models.session import Session
from harness.models.telemetry import HarnessTelemetryRequest
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.claude_telemetry_support import (
    assert_stored_telemetry_usage,
    assert_telemetry_audit,
    claude_cost_usage_body,
    telemetry_gateway,
)
from tests.plugin_tests.support_runtime import interpreting_runtime


def test_claude_otel_delivery_records_raw(tmp_path: Path) -> None:
    """Verify claude otel delivery records raw and canonical audit."""
    runtime, interpreter = interpreting_runtime(tmp_path / fixture.MAIN_DB_PATH)
    runtime.register(
        CLAUDE_CODE_HARNESS,
        Session(
            SessionId(fixture.SESSION_ONE_ID),
            ActorId(fixture.SESSION_ONE_LEAD_ID),
            "/test-data/session-one.jsonl",
            fixture.WORK_PATH,
        ),
    )
    raw_body = claude_cost_usage_body()

    telemetry = telemetry_gateway(runtime)
    assert (
        telemetry.record(
            CLAUDE_CODE_HARNESS,
            HarnessTelemetryRequest("otlp", raw_body),
        )
        == 1
    )
    assert (
        telemetry.record(
            CLAUDE_CODE_HARNESS,
            HarnessTelemetryRequest("otlp", raw_body),
        )
        == 1
    )
    interpreter.tick()

    evidence = runtime.raw_event_audits.audits_for_session(
        SessionId(fixture.SESSION_ONE_ID),
    )
    assert_telemetry_audit(evidence, raw_body)
    assert_stored_telemetry_usage(runtime)

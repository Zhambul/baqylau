# Copyright (c) 2026 Zhambyl Yermagambet
"""Claude telemetry translation tests."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from domain.event_telemetry import UsageReported
from domain.references import ModelReference
from domain.usage import TokenUsage
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import payloads, raw_event

if TYPE_CHECKING:
    from tests.plugin_tests.support_values import JsonValue


def _telemetry_data_point(
    attributes: dict[str, str],
    measurement: float,
) -> dict[str, JsonValue]:
    return {
        "attributes": [
            {"key": key, fixture.VALUE_FIELD: {"stringValue": attribute_value}}
            for key, attribute_value in attributes.items()
        ],
        "asInt" if isinstance(measurement, int) else "asDouble": measurement,
    }


def test_claude_otel_translates_raw_usage_once() -> None:
    """Verify claude otel translates raw usage once by model and query source."""
    document: dict[str, JsonValue] = {
        "resourceMetrics": [
            {
                "scopeMetrics": [
                    {
                        "metrics": [
                            {
                                fixture.NAME_FIELD: "claude_code.token.usage",
                                "sum": {
                                    "dataPoints": [
                                        _telemetry_data_point(
                                            {
                                                fixture.DOTTED_SESSION_ID_FIELD: fixture.SESSION_ONE_ID,
                                                fixture.QUERY_SOURCE_FIELD: fixture.MAIN,
                                                fixture.MODEL: fixture.CLAUDE_OPUS_FOUR_EIGHT,
                                                fixture.TYPE_FIELD: fixture.INPUT_FIELD,
                                            },
                                            10,
                                        ),
                                        _telemetry_data_point(
                                            {
                                                fixture.DOTTED_SESSION_ID_FIELD: fixture.SESSION_ONE_ID,
                                                fixture.QUERY_SOURCE_FIELD: fixture.MAIN,
                                                fixture.MODEL: fixture.CLAUDE_OPUS_FOUR_EIGHT,
                                                fixture.TYPE_FIELD: "cacheRead",
                                            },
                                            7,
                                        ),
                                    ],
                                },
                            },
                            {
                                fixture.NAME_FIELD: "claude_code.cost.usage",
                                "sum": {
                                    "dataPoints": [
                                        _telemetry_data_point(
                                            {
                                                fixture.DOTTED_SESSION_ID_FIELD: fixture.SESSION_ONE_ID,
                                                fixture.QUERY_SOURCE_FIELD: fixture.MAIN,
                                                fixture.MODEL: fixture.CLAUDE_OPUS_FOUR_EIGHT,
                                            },
                                            fixture.QUARTER_SECOND,
                                        ),
                                    ],
                                },
                            },
                        ],
                    },
                ],
            },
        ],
    }
    translation = ClaudeCanonicalTranslator().translate(
        raw_event(
            document,
            harness=CLAUDE_CODE_HARNESS,
            source_type="otel",
            raw_event_id="otel-one",
        ),
    )
    reports = payloads(translation, UsageReported)
    assert len(reports) == 1
    usage = reports[0].payload
    assert usage.model == ModelReference(fixture.CLAUDE_OPUS_FOUR_EIGHT, "opus-4.8")
    assert usage.tokens == TokenUsage()
    assert usage.cost_in_usd == Decimal("0.25")

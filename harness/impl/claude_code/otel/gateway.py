# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide the gateway module."""

# harness/impl/claude_code/otel/gateway.py — what Claude Code's OTLP side
# channel means, decided daemon-side.
#
# Claude Code sends OTLP metrics to a local port. The receiver sends the exact
# bytes to the daemon. This module gives those bytes their meaning.
import hashlib
import time
from collections.abc import Iterator
from typing import override

from domain.ids import HarnessName, RawEventId, SessionId
from harness.contract import HarnessTelemetryGateway
from harness.impl.claude_code.canonical import records
from harness.impl.claude_code.ids import ClaudeCodeSessionId, session_id_from_claude_code
from harness.models.raw_events import (
    RawEvent,
)
from harness.models.telemetry import (
    HarnessTelemetryRequest,
    HarnessTelemetryResponse,
    TelemetryContext,
)

HARNESS = HarnessName("claude_code")
OTLP_KIND = "otlp"


def _session_ids(
    document: records.OTelMetricsDocument,
) -> tuple[SessionId, ...]:
    session_ids = set()
    for point in _metric_points(document):
        native_session_id = point.attribute("session.id")
        if native_session_id:
            session_ids.add(
                session_id_from_claude_code(
                    ClaudeCodeSessionId(str(native_session_id)),
                ),
            )
    return tuple(sorted(session_ids, key=str))


def _metric_points(
    document: records.OTelMetricsDocument,
) -> Iterator[records.OTelDataPoint]:
    for resource in document.resource_metrics:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                if metric.sum is not None:
                    yield from metric.sum.data_points


def _metrics(payload: bytes, telemetry_context: TelemetryContext) -> tuple[RawEvent, ...]:
    document = records.OTelMetricsDocument.model_validate_json(payload)
    raw_events = []
    for session_id in _session_ids(document):
        session = telemetry_context.find_session(session_id)
        if session is None:
            continue
        digest = _event_digest(session_id, payload)
        raw_events.append(
            RawEvent(
                RawEventId(f"claude_code:otel:{digest}"),
                HARNESS,
                "otel",
                "otlp",
                digest,
                session_id,
                session.lead_actor_id,
                None,
                time.time(),
                "json",
                payload,
                f"claude_code:otel:{session_id}",
            ),
        )
    return tuple(raw_events)


class ClaudeTelemetryGateway(HarnessTelemetryGateway):
    """Represent claude telemetry gateway."""

    @override
    def receive_telemetry(
        self,
        harness_telemetry_request: HarnessTelemetryRequest,
        telemetry_context: TelemetryContext,
    ) -> HarnessTelemetryResponse:
        """Receive one telemetry delivery.

        Returns:
            The harness telemetry response.

        """
        if harness_telemetry_request.kind == OTLP_KIND:
            return HarnessTelemetryResponse(
                raw_events=_metrics(harness_telemetry_request.payload, telemetry_context),
            )
        return HarnessTelemetryResponse()


def _event_digest(session_id: SessionId, payload: bytes) -> str:
    digest_input = b"\0".join((str(session_id).encode(), payload))
    return hashlib.sha256(digest_input).hexdigest()

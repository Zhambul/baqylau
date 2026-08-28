# harness/impl/claude_code/otel/gateway.py — what Claude Code's OTLP side
# channel means, decided daemon-side.
#
# Claude Code sends OTLP metrics to a local port. The receiver sends the exact
# bytes to the daemon. This module gives those bytes their meaning.
import hashlib
import time

from domain.ids import HarnessName, RawEventId, SessionId
from harness.contract import HarnessTelemetryGateway
from harness.impl.claude_code.ids import ClaudeCodeSessionId, session_id_from_claude_code
from harness.impl.claude_code.canonical import records
from harness.models import (
    HarnessTelemetryRequest,
    HarnessTelemetryResponse,
    RawEvent,
    TelemetryContext,
)

HARNESS = HarnessName.CLAUDE_CODE
OTLP_KIND = "otlp"


def _session_ids(
    document: records.OTelMetricsDocument,
) -> tuple[SessionId, ...]:
    session_ids = set()
    for resource in document.resourceMetrics:
        for scope in resource.scopeMetrics:
            for metric in scope.metrics:
                for point in metric.sum.dataPoints if metric.sum is not None else ():
                    value = point.attribute("session.id")
                    if value:
                        session_ids.add(
                            session_id_from_claude_code(ClaudeCodeSessionId(str(value)))
                        )
    return tuple(sorted(session_ids, key=str))


class ClaudeTelemetryGateway(HarnessTelemetryGateway):
    def handle(
        self,
        harness_telemetry_request: HarnessTelemetryRequest,
        telemetry_context: TelemetryContext,
    ) -> HarnessTelemetryResponse:
        if harness_telemetry_request.kind == OTLP_KIND:
            return HarnessTelemetryResponse(
                raw_events=self._metrics(harness_telemetry_request.payload, telemetry_context)
            )
        return HarnessTelemetryResponse()

    @staticmethod
    def _metrics(payload: bytes, telemetry_context: TelemetryContext) -> tuple[RawEvent, ...]:
        document = records.OTelMetricsDocument.model_validate_json(payload)
        raw_events = []
        for session_id in _session_ids(document):
            session = telemetry_context.find_session(session_id)
            if session is None:
                continue
            digest = hashlib.sha256(str(session_id).encode() + b"\0" + payload).hexdigest()
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
                )
            )
        return tuple(raw_events)

# harness/impl/claude_code/otel/gateway.py — what Claude Code's two side
# channels mean, decided daemon-side.
#
# Claude Code reports two things no hook payload carries, each on its own
# channel, and neither reachable except by BEING the endpoint:
#
#   otlp        the OTLP metrics export it POSTs to a local port
#   statusline  the rate-limit windows on the status-line command's stdin
#
# Both used to be written to the store by the process that received them — the
# receiver opened events.db, the status-line shim opened usage.db — which made
# them the only writers outside the daemon. Now each ships its exact bytes to
# the daemon's telemetry endpoint and THIS says what they were.
import hashlib
import json
import re
import time
from typing import Any

from domain.ids import HarnessName, RawEventId, SessionId
from harness.contract import HarnessTelemetryGateway
from harness.impl.claude_code import account
from harness.models import (
    AccountUsageSnapshot,
    HarnessTelemetryRequest,
    HarnessTelemetryResponse,
    RawEvent,
    TelemetryContext,
    UsageWindowSample,
)
from decimal import Decimal

HARNESS = HarnessName("claude_code")
OTLP_KIND = "otlp"
STATUSLINE_KIND = "statusline"

# Window-key hygiene: `rate_limits` is external input riding straight into a
# table the dashboard renders — keys must look like window names, and a garbage
# payload must not bloat the table.
_KEY_OK = re.compile(r"^[a-z0-9_]{1,40}$")
KNOWN_WINDOWS = ("five_hour", "seven_day")
MAX_WINDOWS = 8


def _session_ids(document: dict[str, Any]) -> tuple[SessionId, ...]:
    session_ids = set()
    for resource in document.get("resourceMetrics", []):
        for scope in resource.get("scopeMetrics", []):
            for metric in scope.get("metrics", []):
                for point in (metric.get("sum") or {}).get("dataPoints", []):
                    for attribute in point.get("attributes", []):
                        if attribute.get("key") != "session.id":
                            continue
                        value = (attribute.get("value") or {}).get("stringValue")
                        if value:
                            session_ids.add(SessionId(str(value)))
    return tuple(sorted(session_ids, key=str))


def _epoch_seconds(value: object) -> float | None:
    """A rate-limit `resets_at` to epoch SECONDS, or None. Claude Code has sent
    this as either seconds or milliseconds across versions; >1e12 is
    unambiguously milliseconds (a seconds value that large is year ~33000)."""
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        return None
    return value / 1000.0 if value > 1e12 else float(value)


def _percent(value: object) -> Decimal | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return Decimal(max(0, min(100, int(round(value)))))


def windows(document: dict[str, Any]) -> tuple[UsageWindowSample, ...]:
    """Every `rate_limits.<key>.{used_percentage, resets_at}` entry, the
    account-wide pair first and any other window sorted by key.

    Generic over windows on purpose: when Claude Code starts reporting a
    model-scoped one it flows through here and into the dashboard's per-window
    bars with no code change.
    """
    limits = (document or {}).get("rate_limits") or {}
    known = [key for key in KNOWN_WINDOWS if key in limits]
    extra = sorted(key for key in limits if isinstance(key, str) and key not in KNOWN_WINDOWS)
    samples: list[UsageWindowSample] = []
    for key in known + extra:
        if len(samples) >= MAX_WINDOWS:
            break
        window = limits.get(key)
        if not _KEY_OK.match(key) or not isinstance(window, dict):
            continue
        used_percent = _percent(window.get("used_percentage"))
        if used_percent is None:
            continue
        samples.append(
            UsageWindowSample(key, used_percent, _epoch_seconds(window.get("resets_at")))
        )
    return tuple(samples)


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
        if harness_telemetry_request.kind == STATUSLINE_KIND:
            return HarnessTelemetryResponse(usage=self._usage(harness_telemetry_request.payload))
        return HarnessTelemetryResponse()

    @staticmethod
    def _metrics(payload: bytes, telemetry_context: TelemetryContext) -> tuple[RawEvent, ...]:
        document = json.loads(payload)
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

    @staticmethod
    def _usage(payload: bytes) -> AccountUsageSnapshot | None:
        document = json.loads(payload)
        if not isinstance(document, dict):
            return None
        samples = windows(document)
        if not samples:
            # A fresh account before its first API response: leave the last good
            # snapshot in place rather than overwrite it with nothing.
            return None
        # The status-line client stamped its own environment's two account values
        # on the way past, raw and unvalidated (`client/claude_statusline.py`).
        account_id, display_name = account.normalize(
            document.get("_account_id"), document.get("_account_name")
        )
        return AccountUsageSnapshot(
            harness=HARNESS,
            account_id=account_id,
            display_name=display_name,
            captured_at=float(document.get("_ts") or time.time()),
            windows=samples,
        )

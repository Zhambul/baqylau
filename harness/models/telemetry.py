"""Pushed telemetry: evidence a harness reports out-of-band.

The twin of the hook channel. A hook delivery is the harness's own stdin at a
lifecycle moment; a telemetry delivery is a side channel a harness exposes —
OTLP metrics on a local port, rate limits on a status-line command's stdin —
that carries facts no hook payload contains.

Both are shipped to the daemon as EXACT BYTES by a thin client, and both are
recorded daemon-side. Nothing outside the daemon writes the store.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from domain.ids import SessionId
from harness.models.session import Session
from harness.models.evidence import RawEvent
from harness.models.usage import AccountUsageSnapshot

# The channel's own header vocabulary, read only by the endpoint and stamped
# only by the clients that ship a delivery.
TELEMETRY_KIND_HEADER = "X-Baqylau-Telemetry-Kind"
# OTLP exports are batched metric documents; a status-line stdin is small. This
# is generous enough for the former and far above the tiny control-plane cap.
TELEMETRY_MAX = 4 * 1024 * 1024


@dataclass(frozen=True)
class HarnessTelemetryRequest:
    """One delivery: which side channel it came from, and its exact bytes."""

    kind: str
    payload: bytes


@dataclass(frozen=True)
class HarnessTelemetryResponse:
    """What the delivery meant. Both halves are optional: a metrics export is
    evidence, a rate-limit report is a usage snapshot, and an unrecognised
    delivery is neither."""

    raw_events: tuple[RawEvent, ...] = ()
    usage: AccountUsageSnapshot | None = None


class TelemetryContext(Protocol):
    """What a gateway may ask the application while interpreting a delivery.

    Only the session lookup: a telemetry document names sessions by id and the
    gateway has to resolve them, but resolving them is not the gateway's job to
    implement.
    """

    def find_session(self, session_id: SessionId) -> Session | None: ...

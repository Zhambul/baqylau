"""What a session spent and how full its context windows are.

Usage arrives in two shapes and they must not be mixed: an ADDITIVE report is
one increment to sum, a CUMULATIVE one restates a running total, so only the
latest per (scope, subject, model, account) counts.
"""

from __future__ import annotations

from decimal import Decimal
from types import MappingProxyType
from typing import Any

from domain.events import (
    CanonicalEvent,
    CompactionFinished,
    CompactionStarted,
    ContextReported,
    ModelChanged,
    UsageReported,
)
from domain.ids import ActorId
from domain.values import ModelReference, TokenUsage
from engine.projections.models import ContextSummary, ContextWindow, UsageSummary
from domain.records import StoredCanonicalEvent


def usage(stored_events: tuple[StoredCanonicalEvent, ...]) -> UsageSummary:
    session_tokens = TokenUsage()
    session_cost: Decimal | None = None
    by_actor: dict[ActorId, TokenUsage] = {}
    by_model: dict[str, TokenUsage] = {}
    latest_cumulative: dict[tuple[str, str, str, str], UsageReported] = {}
    additive: list[tuple[CanonicalEvent[Any], UsageReported]] = []
    # The usage reports are selected HERE rather than into an intermediate list
    # of events: a filter on `stored.event.payload` that collects
    # `stored.event` throws away the one fact it just established — which
    # payload this event carries — and every field read below then has to be
    # taken on faith. Keeping the narrowed payload in hand is the same loop,
    # one list shorter.
    for stored in stored_events:
        event = stored.event
        report = event.payload
        if not isinstance(report, UsageReported):
            continue
        model_id = report.model.native_id if report.model else ""
        account_id = report.account.account_id if report.account else ""
        if report.cumulative:
            latest_cumulative[(report.scope, report.subject_id, model_id, account_id)] = report
        else:
            additive.append((event, report))
    for event, report in additive:
        if report.scope == "session":
            session_tokens += report.tokens
            if report.cost_in_usd is not None:
                session_cost = (session_cost or Decimal(0)) + report.cost_in_usd
        if report.scope == "actor":
            by_actor[event.actor_id] = by_actor.get(event.actor_id, TokenUsage()) + report.tokens
        if report.model:
            by_model[report.model.native_id] = by_model.get(report.model.native_id, TokenUsage()) + report.tokens
    for report in latest_cumulative.values():
        if report.scope == "session":
            session_tokens += report.tokens
            if report.cost_in_usd is not None:
                session_cost = (session_cost or Decimal(0)) + report.cost_in_usd
        if report.scope == "actor":
            actor_id = ActorId(report.subject_id)
            by_actor[actor_id] = by_actor.get(actor_id, TokenUsage()) + report.tokens
        if report.model:
            by_model[report.model.native_id] = by_model.get(report.model.native_id, TokenUsage()) + report.tokens
    return UsageSummary(
        session_tokens,
        session_cost,
        MappingProxyType(by_actor),
        MappingProxyType(by_model),
    )


def context(stored_events: tuple[StoredCanonicalEvent, ...]) -> ContextSummary:
    windows: dict[ActorId, ContextWindow] = {}
    compacting: set[ActorId] = set()
    models: dict[ActorId, ModelReference] = {}
    for stored in stored_events:
        event = stored.event
        if isinstance(event.payload, ModelChanged):
            models[event.actor_id] = event.payload.current
        elif isinstance(event.payload, ContextReported):
            windows[event.actor_id] = ContextWindow(
                event.payload.used_tokens,
                event.payload.window_tokens,
                event.payload.model or models.get(event.actor_id),
            )
        elif isinstance(event.payload, CompactionStarted):
            compacting.add(event.actor_id)
        elif isinstance(event.payload, CompactionFinished):
            compacting.discard(event.actor_id)
    return ContextSummary(
        MappingProxyType(windows),
        tuple(sorted(compacting, key=str)),
    )

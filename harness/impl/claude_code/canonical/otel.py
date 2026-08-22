"""Claude Code's typed OTLP usage/cost metrics translation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from domain.events import CanonicalEvent, EventPayload, UsageReported
from domain.values import TokenUsage, UsageScope
from harness.impl.claude_code.canonical import records
from harness.impl.claude_code.canonical.support import event, model_reference
from harness.impl.claude_code.model import ClaudeCodeModel
from harness.models import RawEvent


@dataclass
class UsageAmount:
    key: str
    value: Decimal


@dataclass
class UsageGroup:
    model: ClaudeCodeModel | None
    query_source: str
    amounts: list[UsageAmount]

    def add(self, key: str, value: Decimal) -> None:
        amount = next((amount for amount in self.amounts if amount.key == key), None)
        if amount is None:
            self.amounts.append(UsageAmount(key, value))
        else:
            amount.value += value

    def value(self, key: str) -> Decimal:
        amount = next((amount for amount in self.amounts if amount.key == key), None)
        return amount.value if amount is not None else Decimal(0)


def translate_otel(
    raw_event: RawEvent,
    document: records.OTelMetricsDocument,
) -> list[CanonicalEvent[EventPayload]]:
    groups: list[UsageGroup] = []
    for resource in document.resourceMetrics:
        for scope in resource.scopeMetrics:
            for metric in scope.metrics:
                if "token.usage" not in metric.name and "cost.usage" not in metric.name:
                    continue
                for point in metric.sum.dataPoints if metric.sum is not None else ():
                    if str(point.attribute("session.id") or "") != str(raw_event.session_id):
                        continue
                    native_value = point.asDouble if point.asDouble is not None else point.asInt
                    if native_value is None:
                        continue
                    model_text = str(point.attribute("model") or "")
                    model = ClaudeCodeModel(model_text) if model_text else None
                    query_source = str(point.attribute("query_source") or "")
                    group = next(
                        (
                            candidate
                            for candidate in groups
                            if candidate.model == model
                            and candidate.query_source == query_source
                        ),
                        None,
                    )
                    if group is None:
                        group = UsageGroup(model, query_source, [])
                        groups.append(group)
                    usage_type = str(point.attribute("type") or "")
                    key = "cost" if "cost.usage" in metric.name else usage_type
                    group.add(key, Decimal(str(native_value)))

    events = []
    ordered = sorted(groups, key=lambda group: (group.model.value if group.model else "", group.query_source))
    for index, group in enumerate(ordered):
        tokens = TokenUsage(
            input_tokens=int(group.value("input")),
            output_tokens=int(group.value("output")),
            cache_read_tokens=int(group.value("cacheRead")),
            cache_write_tokens=int(group.value("cacheCreation")),
        )
        cost = next(
            (amount.value for amount in group.amounts if amount.key == "cost"),
            None,
        )
        if tokens == TokenUsage() and cost is None:
            continue
        selected_model = (
            model_reference(group.model) if group.model else None
        )
        payload = UsageReported(
            UsageScope.SESSION,
            str(raw_event.session_id),
            selected_model,
            None,
            tokens,
            False,
            cost,
        )
        events.append(
            event(
                raw_event,
                "usage",
                f"{raw_event.source_position}:{index}:{group.model or ''}:{group.query_source}",
                "reported",
                payload,
            )
        )
    return events

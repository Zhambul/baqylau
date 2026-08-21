"""Claude Code's OTel usage/cost metrics stream, translated into usage facts."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from domain.events import CanonicalEvent, EventPayload, UsageReported
from domain.ids import ModelId
from domain.values import TokenUsage
from harness.impl.claude_code.canonical.support import event, model_reference
from harness.models import RawEvent


def translate_otel(raw_event: RawEvent, document: dict[str, Any]) -> list[CanonicalEvent[EventPayload]]:
    grouped: dict[tuple[str, str], dict[str, Decimal]] = {}
    for resource in document.get("resourceMetrics", []):
        for scope in resource.get("scopeMetrics", []):
            for metric in scope.get("metrics", []):
                metric_name = str(metric.get("name") or "")
                if "token.usage" not in metric_name and "cost.usage" not in metric_name:
                    continue
                for point in (metric.get("sum") or {}).get("dataPoints", []):
                    attributes = {}
                    for attribute in point.get("attributes", []):
                        value = attribute.get("value") or {}
                        attributes[str(attribute.get("key") or "")] = next(
                            (value[key] for key in ("stringValue", "intValue", "doubleValue") if key in value),
                            None,
                        )
                    if str(attributes.get("session.id") or "") != str(raw_event.session_id):
                        continue
                    native_value = point.get("asDouble", point.get("asInt"))
                    if native_value is None:
                        continue
                    model_id = str(attributes.get("model") or "")
                    query_source = str(attributes.get("query_source") or "")
                    values = grouped.setdefault((model_id, query_source), {})
                    usage_type = str(attributes.get("type") or "")
                    key = "cost" if "cost.usage" in metric_name else usage_type
                    values[key] = values.get(key, Decimal(0)) + Decimal(str(native_value))

    events = []
    for index, ((model_id, query_source), values) in enumerate(sorted(grouped.items())):
        tokens = TokenUsage(
            input_tokens=int(values.get("input", 0)),
            output_tokens=int(values.get("output", 0)),
            cache_read_tokens=int(values.get("cacheRead", 0)),
            cache_write_tokens=int(values.get("cacheCreation", 0)),
        )
        cost = values.get("cost")
        if tokens == TokenUsage() and cost is None:
            continue
        model = model_reference(ModelId(model_id)) if model_id else None
        payload = UsageReported(
            "session",
            str(raw_event.session_id),
            model,
            None,
            tokens,
            False,
            cost,
        )
        events.append(event(
            raw_event,
            "usage",
            f"{raw_event.source_position}:{index}:{model_id}:{query_source}",
            "reported",
            payload,
        ))
    return events

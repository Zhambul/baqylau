"""Claude Code's OTel usage/cost metrics stream, translated into usage facts."""

from __future__ import annotations

from decimal import Decimal

from pydantic import JsonValue

from domain.events import CanonicalEvent, EventPayload, UsageReported
from domain.ids import ModelId
from domain.values import TokenUsage
from harness.impl.claude_code.canonical import records
from harness.impl.claude_code.canonical.support import event, model_reference
from harness.models import RawEvent


def _dicts(value: JsonValue) -> list[dict[str, JsonValue]]:
    """`value` as a list of objects — the recurring OTLP shape ("every level
    of this tree is a list of objects, or absent"), so every nesting below
    reads the same way regardless of which level is malformed."""
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def translate_otel(
    raw_event: RawEvent,
    document: dict[str, JsonValue],
) -> list[CanonicalEvent[EventPayload]]:
    # Requires only "a JSON object" (records.OTelMetricsDocument, OPEN_FOREIGN
    # — the module header): OTLP is walked generically below with `.get()`/
    # `isinstance` at every level, exactly as it always was.
    records.OTelMetricsDocument.model_validate(document)
    grouped: dict[tuple[str, str], dict[str, Decimal]] = {}
    for resource in _dicts(document.get("resourceMetrics")):
        for scope in _dicts(resource.get("scopeMetrics")):
            for metric in _dicts(scope.get("metrics")):
                metric_name = str(metric.get("name") or "")
                if "token.usage" not in metric_name and "cost.usage" not in metric_name:
                    continue
                metric_sum = metric.get("sum")
                data_points = metric_sum.get("dataPoints") if isinstance(metric_sum, dict) else None
                for point in _dicts(data_points):
                    attributes: dict[str, JsonValue] = {}
                    for attribute in _dicts(point.get("attributes")):
                        value = attribute.get("value")
                        value = value if isinstance(value, dict) else {}
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

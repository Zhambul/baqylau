# Copyright (c) 2026 Zhambyl Yermagambet
"""Claude Code's typed OTLP usage/cost metrics translation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from domain.event_telemetry import UsageReported
from domain.usage import TokenUsage, UsageScope
from harness.impl.claude_code.canonical import support
from harness.impl.claude_code.model_names import record_model

if TYPE_CHECKING:
    from collections.abc import Iterator

    from domain.event_base import CanonicalEvent, EventPayload
    from harness.impl.claude_code.canonical import records
    from harness.models.raw_events import (
        RawEvent,
    )


@dataclass
class UsageAmount:
    """Represent usage amount."""

    key: str
    amount: Decimal


@dataclass
class UsageGroup:
    """Represent usage group."""

    model: str | None
    query_source: str
    amounts: list[UsageAmount]

    def add(self, usage_sample: UsageSample) -> None:
        """Add one usage sample."""
        amount = next(
            (amount for amount in self.amounts if amount.key == usage_sample.key),
            None,
        )
        if amount is None:
            self.amounts.append(UsageAmount(usage_sample.key, usage_sample.amount))
        else:
            amount.amount += usage_sample.amount

    def matches(self, usage_sample: UsageSample) -> bool:
        """Test if a sample belongs to this group.

        Returns:
            True if the sample belongs to this group.

        """
        return self.model == usage_sample.model and self.query_source == usage_sample.query_source

    @property
    def cost(self) -> Decimal | None:
        """The reported cost.

        Returns:
            The reported cost, if present.

        """
        return next(
            (amount.amount for amount in self.amounts if amount.key == "cost"),
            None,
        )

    @property
    def order_key(self) -> tuple[str, str]:
        """The stable group order key.

        Returns:
            The model and query source.

        """
        return self.model or "", self.query_source


@dataclass(frozen=True)
class UsageSample:
    """Represent one decoded OTel usage point."""

    model: str | None
    query_source: str
    key: str
    amount: Decimal

    @classmethod
    def from_point(
        cls,
        raw_event: RawEvent,
        metric_name: str,
        data_point: records.OTelDataPoint,
    ) -> UsageSample | None:
        """Decode one point for the current session.

        Returns:
            The usage sample, if the point belongs to this session.

        """
        point_session_id = str(data_point.attribute("session.id") or "")
        native_amount = data_point.amount()
        if point_session_id == str(raw_event.session_id) and native_amount is not None:
            return cls._from_current_session(metric_name, data_point, native_amount)
        return None

    @classmethod
    def _from_current_session(
        cls,
        metric_name: str,
        data_point: records.OTelDataPoint,
        native_amount: str | float,
    ) -> UsageSample:
        model_name = str(data_point.attribute("model") or "")
        selected_model = record_model(model_name) if model_name else None
        usage_key = str(data_point.attribute("type") or "")
        if "cost.usage" in metric_name:
            usage_key = "cost"
        return cls(
            model=selected_model,
            query_source=str(data_point.attribute("query_source") or ""),
            key=usage_key,
            amount=Decimal(str(native_amount)),
        )


def usage_samples(
    raw_event: RawEvent,
    document: records.OTelMetricsDocument,
) -> Iterator[UsageSample]:
    """Yield usage samples for the current session.

    Yields:
        Each usage sample.

    """
    scopes = (scope for resource in document.resource_metrics for scope in resource.scope_metrics)
    metrics = (metric for scope in scopes for metric in scope.metrics)
    for metric in metrics:
        for data_point in metric.usage_points():
            usage_sample = UsageSample.from_point(raw_event, metric.name, data_point)
            if usage_sample is not None:
                yield usage_sample


def usage_event(
    raw_event: RawEvent,
    group_index: int,
    usage_group: UsageGroup,
) -> CanonicalEvent[EventPayload] | None:
    """Build one provider cost event.

    Returns:
        The cost event, if the group has a cost.

    """
    if usage_group.cost is None:
        return None
    subject_parts = (
        raw_event.source_position,
        str(group_index),
        str(usage_group.model or ""),
        usage_group.query_source,
    )
    payload = UsageReported(
        scope=UsageScope.SESSION,
        subject_id=str(raw_event.session_id),
        model=support.model_reference(usage_group.model) if usage_group.model else None,
        account=None,
        tokens=TokenUsage(),
        cumulative=False,
        cost_in_usd=usage_group.cost,
    )
    return support.event(
        raw_event,
        support.CanonicalEventDraft(
            "usage",
            ":".join(subject_parts),
            "reported",
            payload,
        ),
    )


def translate_otel(
    raw_event: RawEvent,
    document: records.OTelMetricsDocument,
) -> list[CanonicalEvent[EventPayload]]:
    """Translate otel.

    Returns:
        Result items.

    """
    usage_groups: list[UsageGroup] = []
    for sample in usage_samples(raw_event, document):
        usage_group = next(
            (candidate for candidate in usage_groups if candidate.matches(sample)),
            None,
        )
        if usage_group is None:
            usage_group = UsageGroup(sample.model, sample.query_source, [])
            usage_groups.append(usage_group)
        usage_group.add(sample)
    return [
        translated_event
        for group_index, usage_group in enumerate(sorted(usage_groups, key=lambda group: group.order_key))
        if (translated_event := usage_event(raw_event, group_index, usage_group)) is not None
    ]

# Copyright (c) 2026 Zhambyl Yermagambet
"""Translate native step usage without counting cumulative reports twice."""

from domain.event_telemetry import UsageReported
from domain.usage import TokenUsage, UsageScope
from harness.impl.opencode2.records import NativeRecord


def payloads(native_record: NativeRecord) -> tuple[UsageReported, ...]:
    """Read usage from a completed model step.

    Returns:
        One usage delta, or no usage for another event.

    """
    details = native_record.event.details
    tokens = details.tokens
    if native_record.event.type != "session.step.ended" or tokens is None:
        return ()
    return (
        UsageReported(
            UsageScope.SESSION,
            native_record.session.id,
            None,
            None,
            TokenUsage(
                input_tokens=tokens.input,
                output_tokens=tokens.output,
                cache_read_tokens=tokens.cache.read,
                cache_write_tokens=tokens.cache.write,
            ),
            cumulative=False,
            cost_in_usd=details.cost,
        ),
    )

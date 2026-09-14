# Copyright (c) 2026 Zhambyl Yermagambet
"""Report the context used by one completed native model step."""

from domain.event_telemetry import ContextReported
from domain.references import ModelReference
from harness.impl.opencode2.records import NativeRecord
from harness.impl.opencode2.tokens import NativeTokens


def payloads(native_record: NativeRecord) -> tuple[ContextReported, ...]:
    """Read a context report only when the native model limit is known.

    Returns:
        The last step's context use, not a sum across earlier steps.

    """
    context = native_record.context
    tokens = native_record.event.details.tokens
    if native_record.event.type != "session.step.ended" or context is None or tokens is None:
        return ()
    if context.window_tokens is None or context.window_tokens <= 0:
        return ()
    model = context.model
    name = f"{model.provider}/{model.id}"
    return (ContextReported(_used(tokens), context.window_tokens, ModelReference(name, name)),)


def _used(native_tokens: NativeTokens) -> int:
    cached = native_tokens.cache.read + native_tokens.cache.write
    return native_tokens.input + native_tokens.output + native_tokens.reasoning + cached

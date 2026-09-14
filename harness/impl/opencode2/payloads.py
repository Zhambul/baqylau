# Copyright (c) 2026 Zhambyl Yermagambet
"""Collect every fact that one native OpenCode2 record carries.

Each reader answers for one topic and returns nothing for a record that is not
its own. They are listed rather than chained into the translator so that a new
topic is a new row here, not another line in the translation loop.
"""

from domain.event_base import EventPayload
from harness.impl.opencode2 import (
    actors,
    compaction,
    context_usage,
    conversation,
    files,
    questions,
    shells,
    telemetry,
)
from harness.impl.opencode2.permissions import payloads as permission_payloads
from harness.impl.opencode2.records import NativeRecord
from harness.impl.opencode2.skills import payloads as skill_payloads
from harness.impl.opencode2.web import payloads as web_payloads

READERS = (
    conversation.payloads,
    compaction.payloads,
    telemetry.payloads,
    context_usage.payloads,
    shells.payloads,
    actors.payloads,
    questions.payloads,
    permission_payloads,
    files.payloads,
    web_payloads,
    skill_payloads,
)


def payloads(native_record: NativeRecord) -> tuple[EventPayload, ...]:
    """Read every fact carried by one native record.

    Returns:
        The facts in reader order.

    """
    return tuple(payload for reader in READERS for payload in reader(native_record))

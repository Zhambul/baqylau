# Copyright (c) 2026 Zhambyl Yermagambet
"""Show native permission requests and the decisions made in the terminal."""

from types import MappingProxyType

from domain import attention, event_work, ids
from domain.event_base import EventPayload
from harness.impl.opencode2.records import NativeRecord

CHOICES: MappingProxyType[str, str] = MappingProxyType({
    "once": "Allow once", "always": "Always allow", "reject": "Reject",
})


def payloads(native_record: NativeRecord) -> tuple[EventPayload, ...]:
    """Read permission facts from the native event.

    Returns:
        A pending permission question or its recorded answer.

    """
    details = native_record.event.details
    if native_record.event.type == "permission.asked" and details.id:
        return (_asked(native_record),)
    if native_record.event.type != "permission.replied" or not details.request_id:
        return ()
    label = CHOICES.get(details.reply or "")
    if label is None:
        return ()
    answer = attention.AttentionAnswer(ids.QuestionId(details.request_id), (label,))
    return (event_work.QuestionAnswered(ids.AttentionId(details.request_id), (answer,), None),)


def _asked(native_record: NativeRecord) -> event_work.QuestionAsked:
    details = native_record.event.details
    identity = details.id or ""
    text = "\n".join((
        f"Permission required: {details.action}", *details.resources,
    ))
    choices = [attention.AttentionChoice(CHOICES["once"], "Allow this request only")]
    if details.save:
        text = "\n".join((text, "Permanent permission patterns:", *details.save))
        choices.append(attention.AttentionChoice(CHOICES["always"], "Allow these patterns for this project"))
    choices.append(attention.AttentionChoice(CHOICES["reject"], "Do not allow this request"))
    question = attention.AttentionPrompt(
        ids.QuestionId(identity), "Permission", text, multiple=False, choices=tuple(choices),
    )
    return event_work.QuestionAsked(ids.AttentionId(identity), (question,))

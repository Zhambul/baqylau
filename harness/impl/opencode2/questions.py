# Copyright (c) 2026 Zhambyl Yermagambet
"""Translate native question tool input."""

from domain import attention, event_work, ids
from domain.event_base import EventPayload
from harness.impl.opencode2.question_records import NativeQuestion
from harness.impl.opencode2.records import NativeRecord


def payloads(native_record: NativeRecord) -> tuple[EventPayload, ...]:
    """Read a native question with its exact choices.

    Returns:
        The pending question, when the native tool was called.

    """
    tool = native_record.tool
    if tool is None or tool.name != "question" or tool.input is None:
        return ()
    identity = str(native_record.event.details.id)
    if native_record.event.type == "session.tool.success":
        return _answered(native_record, identity)
    if native_record.event.type == "session.tool.failed":
        return _failed(native_record, identity)
    if native_record.event.type != "session.tool.called":
        return ()
    return (_asked(tool.input.questions, identity),)


def _asked(questions: tuple[NativeQuestion, ...], identity: str) -> event_work.QuestionAsked:
    indexed = enumerate(questions)
    prompts = tuple(_prompt(question, identity, index) for index, question in indexed)
    return event_work.QuestionAsked(ids.AttentionId(identity), prompts)


def _prompt(native_question: NativeQuestion, identity: str, index: int) -> attention.AttentionPrompt:
    return attention.AttentionPrompt(
        ids.QuestionId(f"{identity}:{index}"), native_question.header, native_question.question,
        native_question.multiple,
        tuple(attention.AttentionChoice(choice.label, choice.description) for choice in native_question.options),
    )


def _answered(native_record: NativeRecord, identity: str) -> tuple[EventPayload, ...]:
    metadata = native_record.event.details.metadata
    if metadata is None:
        return ()
    answers = tuple(
        attention.AttentionAnswer(ids.QuestionId(f"{identity}:{index}"), labels)
        for index, labels in enumerate(metadata.answers)
    )
    return (event_work.QuestionAnswered(ids.AttentionId(identity), answers, None),)


def _failed(native_record: NativeRecord, identity: str) -> tuple[EventPayload, ...]:
    error = native_record.event.details.error
    if error is None:
        return ()
    return (event_work.QuestionAnswered(ids.AttentionId(identity), (), error.message),)

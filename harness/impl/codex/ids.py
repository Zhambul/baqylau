"""Codex's native identifiers."""

from typing import NewType

from domain.ids import (
    ActorId,
    AssignmentId,
    AttentionId,
    MessageId,
    QuestionId,
    ReasoningId,
    SessionId,
    ShellId,
    TaskId,
    TaskListId,
    TurnId,
)

CodexSessionId = NewType("CodexSessionId", str)
CodexActorId = NewType("CodexActorId", str)
CodexAttentionId = NewType("CodexAttentionId", str)
CodexCallId = NewType("CodexCallId", str)
CodexMessageId = NewType("CodexMessageId", str)
CodexQuestionId = NewType("CodexQuestionId", str)
CodexReasoningId = NewType("CodexReasoningId", str)
CodexShellId = NewType("CodexShellId", str)
CodexTaskId = NewType("CodexTaskId", str)
CodexTaskListId = NewType("CodexTaskListId", str)
CodexTurnId = NewType("CodexTurnId", str)


def session_id_from_codex(codex_session_id: CodexSessionId) -> SessionId:
    return SessionId(codex_session_id)


def codex_session_id_from_domain(session_id: SessionId) -> CodexSessionId:
    return CodexSessionId(session_id)


def actor_id_from_codex(codex_actor_id: CodexActorId) -> ActorId:
    return ActorId(codex_actor_id)


def lead_actor_id_from_codex(codex_session_id: CodexSessionId) -> ActorId:
    return ActorId(f"{codex_session_id}:lead")


def shell_id_from_codex_call(codex_call_id: CodexCallId) -> ShellId:
    return ShellId(codex_call_id)


def attention_id_from_codex_call(codex_call_id: CodexCallId) -> AttentionId:
    return AttentionId(codex_call_id)


def message_id_from_codex_call(codex_call_id: CodexCallId) -> MessageId:
    return MessageId(codex_call_id)


def message_id_from_codex(codex_message_id: CodexMessageId) -> MessageId:
    return MessageId(codex_message_id)


def reasoning_id_from_codex(codex_reasoning_id: CodexReasoningId) -> ReasoningId:
    return ReasoningId(codex_reasoning_id)


def task_id_from_codex(codex_task_id: CodexTaskId) -> TaskId:
    return TaskId(codex_task_id)


def task_list_id_from_codex(codex_task_list_id: CodexTaskListId) -> TaskListId:
    return TaskListId(codex_task_list_id)


def turn_id_from_codex(codex_turn_id: CodexTurnId) -> TurnId:
    return TurnId(codex_turn_id)


def assignment_id_from_codex_turn(codex_turn_id: CodexTurnId) -> AssignmentId:
    return AssignmentId(codex_turn_id)


def question_id_from_codex(codex_question_id: CodexQuestionId) -> QuestionId:
    return QuestionId(codex_question_id)


def attention_id_from_codex(codex_attention_id: CodexAttentionId) -> AttentionId:
    return AttentionId(codex_attention_id)

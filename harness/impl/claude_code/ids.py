"""Claude Code's native identifiers."""

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
    SkillId,
    TaskId,
    TaskListId,
    TurnId,
)

ClaudeCodeSessionId = NewType("ClaudeCodeSessionId", str)
ClaudeCodeActorId = NewType("ClaudeCodeActorId", str)
ClaudeCodeCallId = NewType("ClaudeCodeCallId", str)
ClaudeCodeMessageId = NewType("ClaudeCodeMessageId", str)
ClaudeCodeQuestionId = NewType("ClaudeCodeQuestionId", str)
ClaudeCodeReasoningId = NewType("ClaudeCodeReasoningId", str)
ClaudeCodeShellId = NewType("ClaudeCodeShellId", str)
ClaudeCodeTaskId = NewType("ClaudeCodeTaskId", str)
ClaudeCodeTaskListId = NewType("ClaudeCodeTaskListId", str)
ClaudeCodeTurnId = NewType("ClaudeCodeTurnId", str)
ClaudeCodeControlRequestId = NewType("ClaudeCodeControlRequestId", str)


def session_id_from_claude_code(
    claude_code_session_id: ClaudeCodeSessionId,
) -> SessionId:
    return SessionId(claude_code_session_id)


def claude_code_session_id_from_domain(session_id: SessionId) -> ClaudeCodeSessionId:
    return ClaudeCodeSessionId(session_id)


def actor_id_from_claude_code(claude_code_actor_id: ClaudeCodeActorId) -> ActorId:
    return ActorId(claude_code_actor_id)


def lead_actor_id_from_claude_code(
    claude_code_session_id: ClaudeCodeSessionId,
) -> ActorId:
    return ActorId(f"{claude_code_session_id}:lead")


def shell_id_from_claude_code_call(claude_code_call_id: ClaudeCodeCallId) -> ShellId:
    return ShellId(claude_code_call_id)


def skill_id_from_claude_code_call(claude_code_call_id: ClaudeCodeCallId) -> SkillId:
    return SkillId(claude_code_call_id)


def assignment_id_from_claude_code_call(claude_code_call_id: ClaudeCodeCallId) -> AssignmentId:
    return AssignmentId(claude_code_call_id)


def attention_id_from_claude_code_call(claude_code_call_id: ClaudeCodeCallId) -> AttentionId:
    return AttentionId(claude_code_call_id)


def message_id_from_claude_code_call(claude_code_call_id: ClaudeCodeCallId) -> MessageId:
    return MessageId(claude_code_call_id)


def message_id_from_claude_code(claude_code_message_id: ClaudeCodeMessageId) -> MessageId:
    return MessageId(claude_code_message_id)


def reasoning_id_from_claude_code(
    claude_code_reasoning_id: ClaudeCodeReasoningId,
) -> ReasoningId:
    return ReasoningId(claude_code_reasoning_id)


def shell_id_from_claude_code(claude_code_shell_id: ClaudeCodeShellId) -> ShellId:
    return ShellId(claude_code_shell_id)


def task_id_from_claude_code(claude_code_task_id: ClaudeCodeTaskId) -> TaskId:
    return TaskId(claude_code_task_id)


def task_list_id_from_claude_code(
    claude_code_task_list_id: ClaudeCodeTaskListId,
) -> TaskListId:
    return TaskListId(claude_code_task_list_id)


def turn_id_from_claude_code(claude_code_turn_id: ClaudeCodeTurnId) -> TurnId:
    return TurnId(claude_code_turn_id)


def question_id_from_claude_code(
    claude_code_question_id: ClaudeCodeQuestionId,
) -> QuestionId:
    return QuestionId(claude_code_question_id)

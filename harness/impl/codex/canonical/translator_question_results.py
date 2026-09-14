# Copyright (c) 2026 Zhambyl Yermagambet
"""Split Codex canonical translation."""

from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass

from harness.impl.codex.canonical import translator_dependencies as dependencies
from harness.impl.codex.canonical.translator_answers import _question_answers
from harness.impl.codex.canonical.translator_core_values import QUESTION_SUBJECT
from harness.impl.codex.canonical.translator_identity import CodexToolKind
from harness.impl.codex.canonical.translator_tool_parsing import codex_tool


def question_result(
    raw_event: dependencies.translator_service_dependencies.raw_events.RawEvent,
    ask_record: dependencies.record_canonical_namespaces.record_interaction_records.AskRecord,
    exec_result_record: dependencies.record_canonical_namespaces.record_tool_records.ExecResultRecord,
    occurred_at: float | None,
) -> list[
    dependencies.translator_type_dependencies.event_base.CanonicalEvent[
        dependencies.translator_type_dependencies.event_base.EventPayload
    ]
]:
    """Build the answer event for a native question result.

    Returns:
        One answer event, with no answers if the question was interrupted.

    """
    attention_id = dependencies.translator_id_dependencies.ids_conversation.attention_id_from_codex_call(
        ask_record.call_id,
    )
    answers: tuple[dependencies.translator_domain_events.attention.AttentionAnswer, ...] = ()
    if not exec_result_record.interrupted:
        document = _ask_result_document(exec_result_record.output)
        if document is not None and document.answers.root:
            answers = _question_answers(ask_record, document)
    payload = dependencies.translator_domain_values.event_work.QuestionAnswered(attention_id, answers, None)
    return [
        dependencies.translator_codex_dependencies.support.event(
            raw_event,
            dependencies.translator_service_dependencies.CanonicalEventDraft(
                QUESTION_SUBJECT,
                str(attention_id),
                "answered",
                payload,
                occurred_at=occurred_at,
            ),
        ),
    ]


@dataclass(frozen=True)
class CodexToolResult:
    """Store a tool result with its canonical kind and outcome."""

    record: dependencies.record_canonical_namespaces.record_tool_records.ToolRecord
    kind: CodexToolKind
    native_name: str
    output: str
    outcome: dependencies.translator_domain_values.outcomes.Outcome


def codex_tool_result(
    tool_record: dependencies.record_canonical_namespaces.record_tool_records.ToolRecord,
    exec_result_record: dependencies.record_canonical_namespaces.record_tool_records.ExecResultRecord,
    mcp_outcome: dependencies.translator_domain_values.outcomes.Outcome | None,
) -> CodexToolResult:
    """Combine native tool output and failure data.

    Returns:
        The tool record, canonical kind, output, and final outcome.

    """
    kind, native_name = codex_tool(tool_record.name, tool_record.args)
    output, native_failed = _node_tool_output(tool_record, exec_result_record.output)
    outcome = (
        dependencies.translator_domain_values.outcomes.Outcome.FAILED
        if native_failed
        or mcp_outcome == dependencies.translator_domain_values.outcomes.Outcome.FAILED
        or dependencies.translator_codex_dependencies.support.exit_code(exec_result_record.exit) not in {None, 0}
        else dependencies.translator_domain_values.outcomes.Outcome.SUCCEEDED
    )
    return CodexToolResult(tool_record, kind, native_name, output, outcome)


def _node_tool_output(
    tool_record: dependencies.record_canonical_namespaces.record_tool_records.ToolRecord, output: str,
) -> tuple[str, bool]:
    if tool_record.name != "mcp__node_repl__js":
        return output, False
    try:
        node_result = (
            dependencies.record_payload_namespaces.record_response_parts.NodeReplResultDocument.model_validate_json(
                output,
            )
        )
    except dependencies.translator_service_dependencies.ValidationError:
        # The custom-exec wrapper can print the MCP text directly.
        return output, False
    return _node_result_text(node_result), node_result.is_error


def _node_result_text(
    node_result: dependencies.record_payload_namespaces.record_response_parts.NodeReplResultDocument,
) -> str:
    text_parts = [part.text for part in node_result.content if part.text is not None]
    return "\n".join(text_parts)


def resolved_tool_path(path: str, working_directory: str | None) -> str:
    """Resolve a relative tool path when the working directory is known.

    Returns:
        The resolved path, or the supplied path when no resolution is needed.

    """
    if pathlib.Path(path).is_absolute() or not working_directory:
        return path
    return os.path.normpath(str(pathlib.Path(working_directory) / path))


def _ask_result_document(
    output: str,
) -> dependencies.record_payload_namespaces.record_plan_arguments.AskResultDocument | None:
    try:
        return dependencies.record_payload_namespaces.record_plan_arguments.AskResultDocument.model_validate_json(
            output,
        )
    except dependencies.translator_service_dependencies.ValidationError:
        if output.strip() == "request_user_input can only be used by the root thread":
            return None
        raise

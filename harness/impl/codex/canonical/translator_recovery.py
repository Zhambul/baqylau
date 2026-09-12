# Copyright (c) 2026 Zhambyl Yermagambet
"""Split Codex canonical translation."""

from __future__ import annotations

import contextlib
import pathlib
from dataclasses import dataclass, field
from typing import BinaryIO

from harness.impl.codex.canonical import translator_batch_results, translator_dependencies as dependencies
from harness.impl.codex.canonical.record_tool_headers import ToolCallHeaderDocument
from harness.impl.codex.canonical.translator_core_values import (
    BACKWARD_SCAN_CHUNK_BYTES,
    BINARY_READ_MODE,
)
from harness.impl.codex.canonical.translator_shell_state import _REPORTED_PROCESS_ID
from harness.impl.codex.canonical.translator_tool_paths import read_skill_name

MINIMUM_SHELL_ARGUMENTS = 3


def backward_lines(source_path: str, end_position: int) -> dependencies.translator_type_dependencies.Iterator[bytes]:
    """Read source lines in reverse order up to a byte position.

    Yields:
        Lines without line endings, until the file ends or a read fails.

    """
    with contextlib.suppress(OSError):
        yield from _read_backward_lines(source_path, end_position)


def _read_backward_lines(
    source_path: str, end_position: int,
) -> dependencies.translator_type_dependencies.Iterator[bytes]:
    with pathlib.Path(source_path).open(BINARY_READ_MODE) as source:
        partial_line = b""
        while end_position > 0:
            start_position = max(0, end_position - BACKWARD_SCAN_CHUNK_BYTES)
            source.seek(start_position)
            lines = (source.read(end_position - start_position) + partial_line).splitlines(keepends=True)
            partial_line = lines.pop(0) if start_position and lines else b""
            yield from (line.rstrip(b"\r\n") for line in reversed(lines))
            end_position = start_position


def _matching_process_record(
    line: bytes,
    result_calls: dependencies.translator_type_dependencies.MutableMapping[
        dependencies.translator_id_dependencies.ids_session_types.CodexCallId, translator_batch_results.ProcessResult,
    ],
) -> dependencies.record_canonical_namespaces.record_terminal_records.RolloutRecord | None:
    header = ToolCallHeaderDocument.model_validate_json(line)
    if header.type != "response_item":
        return None
    payload = header.payload
    result_record = payload.type in {"function_call_output", "custom_tool_call_output"}
    matching_call = payload.type in {"function_call", "custom_tool_call"} and payload.call_id in result_calls
    if not result_record and not matching_call:
        return None
    return dependencies.translator_codex_dependencies.rollout.parse_line(line.decode())


def process_shell_call_from_line(
    line: bytes,
    process_id: dependencies.translator_id_dependencies.ids_session_types.CodexShellId,
    result_calls: dependencies.translator_type_dependencies.MutableMapping[
        dependencies.translator_id_dependencies.ids_session_types.CodexCallId, translator_batch_results.ProcessResult,
    ],
) -> dependencies.record_canonical_namespaces.record_tool_records.ExecRecord | None:
    """Track process results and find the matching shell call.

    Returns:
        The matching call, or None if this line does not identify it.

    """
    try:
        record = _matching_process_record(line, result_calls)
    except (UnicodeDecodeError, dependencies.translator_service_dependencies.ValidationError):
        return None
    if isinstance(record, dependencies.record_canonical_namespaces.record_tool_records.ExecResultRecord):
        _record_process_result(record, process_id, result_calls)
        return None
    if isinstance(record, dependencies.record_canonical_namespaces.record_actor_records.ToolBatchRecord):
        return translator_batch_results.process_call(record, result_calls.get(record.call_id, False), process_id)
    if (
        not isinstance(record, dependencies.record_canonical_namespaces.record_tool_records.ExecRecord)
        or record.call_id not in result_calls
    ):
        return None
    matched = result_calls[record.call_id]
    return record if matched is False or (matched is True and record.reports_session_id) else None


def _record_process_result(
    record: dependencies.record_canonical_namespaces.record_tool_records.ExecResultRecord,
    process_id: dependencies.translator_id_dependencies.ids_session_types.CodexShellId,
    result_calls: dependencies.translator_type_dependencies.MutableMapping[
        dependencies.translator_id_dependencies.ids_session_types.CodexCallId, translator_batch_results.ProcessResult,
    ],
) -> None:
    if record.running and record.process_id == process_id:
        result_calls[record.call_id] = False
        return
    reported_process = _REPORTED_PROCESS_ID.fullmatch(record.output.strip())
    if reported_process is not None and reported_process.group(1) == process_id:
        result_calls[record.call_id] = True
    elif process_id in record.output:
        result_calls[record.call_id] = record


def command_texts(native_command: tuple[str, ...]) -> set[str]:
    """Get command text forms used to match native shell records.

    Returns:
        The joined arguments and, when present, the shell command argument.

    """
    command_texts = {" ".join(native_command)}
    if len(native_command) < MINIMUM_SHELL_ARGUMENTS:
        return command_texts
    if native_command[-2] in {"-c", "-lc"}:
        command_texts.add(native_command[-1])
    return command_texts


@dataclass
class _PendingExecRecovery:
    command_texts: set[str]
    pending: list[dependencies.record_canonical_namespaces.record_tool_records.ExecRecord]
    batches: list[dependencies.record_canonical_namespaces.record_actor_records.ToolBatchRecord] = field(
        default_factory=list,
    )

    def read(self, source: BinaryIO, end_position: int) -> None:
        """Read complete records up to the requested byte position."""
        while source.tell() < end_position:
            line = source.readline()
            if not line:
                break
            self._observe_line(line)

    def observe(
        self,
        record: dependencies.record_canonical_namespaces.record_terminal_records.RolloutRecord | None,
    ) -> None:
        if isinstance(record, dependencies.record_canonical_namespaces.record_actor_records.ToolBatchRecord):
            self._observe_batch(record)
            return
        if isinstance(record, dependencies.record_canonical_namespaces.record_tool_records.ExecRecord):
            if read_skill_name(record.cmd) is None and record.cmd in self.command_texts:
                self.pending.append(record)
            return
        if isinstance(record, dependencies.record_canonical_namespaces.record_tool_records.ExecResultRecord):
            self._observe_result(record)
            return
        if (
            isinstance(
                record,
                dependencies.record_canonical_namespaces.record_tool_records.CommandCompletedRecord,
            )
            and record.command
        ) and self.command_texts & command_texts(record.command) and self.pending:
            self.pending.pop(0)

    def _observe_batch(
        self, record: dependencies.record_canonical_namespaces.record_actor_records.ToolBatchRecord,
    ) -> None:
        if record.call_id is not None:
            self.batches.append(record)
        for action in record.actions:
            self.observe(action)

    def _observe_result(
        self, record: dependencies.record_canonical_namespaces.record_tool_records.ExecResultRecord,
    ) -> None:
        for batch in self.batches:
            if batch.call_id != record.call_id:
                continue
            self.batches.remove(batch)
            for nested in translator_batch_results.command_results(batch, record):
                self.observe(nested)
            break
        self._remove_result(record)

    def _observe_line(self, line: bytes) -> None:
        try:
            record = dependencies.translator_codex_dependencies.rollout.parse_line(line.decode())
        except (UnicodeDecodeError, dependencies.translator_service_dependencies.ValidationError):
            return
        self.observe(record)

    def _remove_result(
        self, result: dependencies.record_canonical_namespaces.record_tool_records.ExecResultRecord,
    ) -> None:
        for index, call_record in enumerate(self.pending):
            if call_record.call_id != result.call_id:
                continue
            yielded = call_record.yield_ms is not None and result.exit is None and not result.output
            if not result.running and not yielded:
                self.pending.pop(index)
            return

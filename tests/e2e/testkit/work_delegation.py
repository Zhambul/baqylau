# Copyright (c) 2026 Zhambyl Yermagambet
"""Build single-work delegation prompts for E2E journeys."""

from __future__ import annotations

import json
from types import MappingProxyType
from typing import TYPE_CHECKING

from tests.e2e.testkit import work_names
from tests.e2e.testkit.references import WorkerKind

if TYPE_CHECKING:
    from collections.abc import Mapping

    from tests.e2e.testkit.work_models import WorkRequest

CODEX_HARNESS = "codex"
CLAUDE_CODE_HARNESS = "claude_code"
OPENCODE2_HARNESS = "opencode2"
ATTACHMENT_INSTRUCTION = (
    "Also pass any attached file content to the subagent. "
    "If the attachment supplies a file path instead of content, pass that exact path. "
)


# How each harness asks for MORE THAN ONE subagent in a single response. The
# wording is native to each tool, so it belongs beside the other names rather
# than inside the builder: a new harness adds a row, not a branch. Parallel work
# is always background work: the point of it is that the siblings run together.
PARALLEL_INSTRUCTIONS: Mapping[str, str] = MappingProxyType({
    CODEX_HARNESS: (
        "Use spawn_agent once for every work item below. "
        "Make all spawn calls in one response so the subagents run in parallel. "
        "For each call, set task_name to the stated worker name and set message "
        "to the exact text between WORK START and WORK END. Do not do the work "
        "yourself. Do not use wait_agent. After all subagents start, reply only "
        "with the word launched."
    ),
    CLAUDE_CODE_HARNESS: (
        "Use the Agent tool once for every work item below. Put all Agent calls "
        "in one response so the subagents run in parallel. For each call, set "
        "description to the stated work name, set run_in_background to true, "
        "and set prompt to the exact text between WORK START and WORK END. Do "
        "not set name. Do not do the work yourself. Each Agent call returns an "
        "async launch acknowledgement. Immediately after the final launch "
        "acknowledgement, reply only with the word launched. Do not wait for "
        "child completion or notifications."
    ),
    # The subagents here WAIT. A background child of OpenCode2 announces itself
    # in the turn that asked for it, and the lead answers that announcement in
    # the same turn, so one turn would hold more than one answer. The background
    # contract has its own scenarios; this one is about two subagents at once.
    OPENCODE2_HARNESS: (
        "Use the subagent tool once for every work item below. Make all subagent "
        "calls in one response so the subagents run in parallel. For each call, "
        "do not set background, set description to the stated work name, and set "
        "prompt to the exact text between WORK START and WORK END. Do not do the "
        "work yourself. After every call returns, reply only with the word "
        "launched. Reply exactly once."
    ),
})


def delegation_prompt(harness: str, request: WorkRequest) -> str:
    """Return the prompt that delegates one work request.

    Returns:
        The prompt that delegates one work request.

    Raises:
        AssertionError: If subagent work is requested for an unsupported harness.

    """
    if request.worker_kind.value == "lead":
        return request.prompt
    prompt = request.prompt
    name = work_names.worker_name(request.name)
    background = request.background
    if harness == CODEX_HARNESS:
        return codex_prompt(name, prompt, background=background)
    if harness == CLAUDE_CODE_HARNESS:
        return claude_prompt(name, prompt, named=request.named, background=background)
    if harness == OPENCODE2_HARNESS:
        return opencode_prompt(name, prompt, background=background)
    message = f"harness {harness!r} has no subagent work adapter"
    raise AssertionError(message)


def request_prompt(harness: str, request: WorkRequest) -> str:
    """Return the prompt for one work request.

    Returns:
        The prompt for one work request.

    """
    if request.worker_kind == WorkerKind.LEAD:
        return request.prompt
    return delegation_prompt(harness, request)


def codex_prompt(name: str, prompt: str, *, background: bool) -> str:
    """Return the Codex single-work prompt.

    Codex has no background flag. Its spawn_agent always returns at once, and
    wait_agent is the separate tool that blocks for the result.

    Returns:
        The Codex single-work prompt.

    """
    encoded_message = json.dumps(prompt).replace("$", r"\u0024")
    ending = (
        "Do not use another tool. After the subagent starts, reply only with the word delegated."
        if background
        else (
            f"After the subagent starts, use wait_agent exactly once. Set target to '/root/{name}'. "
            "After wait_agent returns the subagent result, reply only with the word delegated."
        )
    )
    instruction = (
        "Use spawn_agent exactly once. "
        f"Set task_name to {name!r}. Decode WORK MESSAGE JSON as JSON and "
        f"set message to the decoded string exactly. {ATTACHMENT_INSTRUCTION}Do not do the work yourself. {ending}"
    )
    return f"{instruction}\n\nWORK MESSAGE JSON\n{encoded_message}"


def claude_prompt(name: str, prompt: str, *, named: bool, background: bool) -> str:
    """Return the Claude single-work prompt.

    The Agent tool runs in the background unless run_in_background is false.

    Returns:
        The Claude single-work prompt.

    """
    name_instruction = f"Set name to {name!r}. " if named else "Do not set name. "
    mode = (
        "Set run_in_background to true. Do not wait for the subagent result. "
        if background
        else "Set run_in_background to false. Wait for the subagent result. "
    )
    instruction = (
        "Use the Agent tool exactly once. "
        f"Use description {name!r}. Give the subagent the exact work text between WORK START and WORK END. "
        f"{ATTACHMENT_INSTRUCTION}Do not do the work yourself. {name_instruction}{mode}Do not use another tool. "
        "After the Agent tool returns, reply only with the word delegated."
    )
    return f"{instruction}\n\nWORK START\n{prompt}\nWORK END"


def opencode_prompt(name: str, prompt: str, *, background: bool) -> str:
    """Request one native OpenCode subagent.

    The native subagent tool waits for its result unless background is true.

    Returns:
        The exact work text inside a delegation instruction.

    """
    mode = (
        "Set background to true. Do not wait for the subagent result. "
        if background
        else "Do not set background. Wait for the subagent result. "
    )
    instruction = (
        "Use the native subagent tool exactly once. "
        f"Use description {name!r}. Give the subagent the exact work text between WORK START and WORK END. "
        f"{ATTACHMENT_INSTRUCTION}Do not do the work yourself. {mode}Do not use another tool. "
        "After the subagent tool returns, reply only with the word delegated."
    )
    return f"{instruction}\n\nWORK START\n{prompt}\nWORK END"

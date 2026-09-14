# Copyright (c) 2026 Zhambyl Yermagambet
"""Start named work on a lead or on one real subagent."""

from __future__ import annotations

import json
from http import HTTPStatus
from typing import TYPE_CHECKING, Protocol

from sdk.client import SessionLaunchRequest
from tests.e2e.testkit import (
    selector_actors,
    selector_assignment_turns,
    selector_assignments,
    selector_turns,
    work_delegation,
    work_names,
)
from tests.e2e.testkit.references import (
    AssignmentRef,
    SessionSpec,
    TurnRef,
    WorkerControlRef,
    WorkerKind,
    WorkerRef,
    WorkRef,
)
from tests.e2e.testkit.work_models import (
    StartedParallelWork,
    StartedWork,
    WorkRequest,
)

WORKER_NAME_CHARACTER_LIMIT = 40
MINIMUM_PARALLEL_WORK_ITEMS = 2
CODEX_HARNESS = "codex"
CLAUDE_CODE_HARNESS = "claude_code"
OPENCODE2_HARNESS = "opencode2"
# How one parallel assignment is told apart from its siblings. Codex gives the
# child a native name we ask for and can predict. The others do not, so the
# exact prompt is the only stable key: two siblings never share one prompt.
NAMED_PARALLEL_HARNESSES = frozenset((CODEX_HARNESS,))
MISSING_LEAD_ACTOR = "work request does not have a lead actor"

if TYPE_CHECKING:
    from sdk.client import BaqylauClient, SessionRef
    from tests.e2e.testkit.policy import WaitPolicy


def _parallel_delegation_prompt(
    harness: str,
    requests: tuple[WorkRequest, ...],
) -> str:
    if len(requests) < MINIMUM_PARALLEL_WORK_ITEMS:
        message = "parallel work requires at least two work items"
        raise AssertionError(message)
    names = [work_names.worker_name(request.name) for request in requests]
    if len(set(names)) != len(names):
        message = "parallel work names must have distinct native names"
        raise AssertionError(message)
    instruction = work_delegation.PARALLEL_INSTRUCTIONS.get(harness)
    if instruction is None:
        message = f"harness {harness!r} has no subagent work adapter"
        raise AssertionError(message)
    blocks = "\n\n".join(_work_request_block(harness, request) for request in requests)
    return f"{instruction}\n\n{blocks}"


def _work_request_block(harness: str, request: WorkRequest) -> str:
    name_line = f"WORK NAME: {request.name}\n"
    if harness == CODEX_HARNESS:
        name_line = f"WORKER NAME: {work_names.worker_name(request.name)}\n"
    return f"{name_line}WORK START\n{request.prompt}\nWORK END"


def _delegation_with_followup_prompt(
    harness: str,
    work_name: str,
    work_prompt: str,
    followup: str,
) -> str:
    name = work_names.worker_name(work_name)
    if harness == CODEX_HARNESS:
        work_json = json.dumps(work_prompt).replace("$", r"\u0024")
        followup_json = json.dumps(followup).replace("$", r"\u0024")
        return (
            "Use spawn_agent exactly once. "
            f"Set task_name to {name!r}. Decode WORK MESSAGE JSON as JSON and "
            "set message to the decoded string exactly. After the subagent "
            "starts, use followup_task exactly once. Set target to "
            f"'/root/{name}'. Decode FOLLOW-UP JSON as JSON and set message "
            "to the decoded string exactly. Do not do the work yourself. "
            "After the follow-up request returns, reply only with FOLLOWUP_SENT."
            f"\n\nWORK MESSAGE JSON\n{work_json}"
            f"\n\nFOLLOW-UP JSON\n{followup_json}"
        )
    if harness == CLAUDE_CODE_HARNESS:
        return (
            "Use the Agent tool exactly once. "
            f"Use description {name!r}. Give the subagent the exact work text "
            "between WORK START and WORK END. After the asynchronous Agent "
            "launch returns, use SendMessage exactly once. Set `to` to the "
            "agentId returned by Agent. Set `message` to the exact text between "
            "FOLLOW-UP START and FOLLOW-UP END. Use summary 'E2E follow-up'. "
            "Do not do the work yourself. After SendMessage returns, reply only "
            "with FOLLOWUP_SENT."
            f"\n\nWORK START\n{work_prompt}\nWORK END"
            f"\n\nFOLLOW-UP START\n{followup}\nFOLLOW-UP END"
        )
    # OpenCode2 has no tool that messages a subagent while it still runs. Its
    # subagent tool takes a sessionID, but that RESUMES a conversation which
    # already gave its result. So a follow-up mid-run has no native gesture.
    message = f"harness {harness!r} has no subagent follow-up adapter"
    raise AssertionError(message)


def _child_to_lead_message_prompt(
    harness: str,
    message: str,
    result: str,
) -> str:
    if harness == CLAUDE_CODE_HARNESS:
        return (
            "Use SendMessage exactly once. Set `to` to 'team-lead'. Set `message` "
            "to the exact text between PARENT MESSAGE START and PARENT MESSAGE "
            "END. Do not use another tool. After the message request returns, "
            f"reply only with {result}."
            f"\n\nPARENT MESSAGE START\n{message}\nPARENT MESSAGE END"
        )
    message = f"harness {harness!r} has no actor message adapter"
    raise AssertionError(message)


class _WorkResolutionContext(Protocol):
    _client: BaqylauClient
    _wait_policy: WaitPolicy


class WorkResolution:
    """Resolve work requests to live work references."""

    def _resolve(
        self: _WorkResolutionContext,
        request_turn: TurnRef,
        harness: str,
        request: WorkRequest,
    ) -> WorkRef:
        watch = self._client.sessions.watch(request_turn.session)
        request_turn = selector_turns.turn(watch, request_turn, self._wait_policy.turn)
        if request_turn.actor_id is None:
            raise AssertionError(MISSING_LEAD_ACTOR)
        if request.worker_kind == WorkerKind.LEAD:
            return WorkRef(
                request_turn.session,
                request.prompt,
                request_turn,
                WorkerRef(request_turn.session, WorkerKind.LEAD, request_turn.actor_id),
                request_turn,
            )
        assignment = selector_assignments.assignment(
            watch,
            turn_reference=request_turn,
            exact_actor_name=work_names.expected_actor_name(harness, request),
            exact_prompt=request.exact_prompt,
            timeout=self._wait_policy.turn,
        )
        actor = selector_actors.actor_from_assignment(
            watch,
            assignment_reference=assignment,
            timeout=self._wait_policy.background,
        )
        child_turn = selector_assignment_turns.actor_assignment_turn(
            watch,
            actor_reference=actor,
            assignment_reference=assignment,
            requested_prompt=request.prompt,
            timeout=self._wait_policy.turn,
        )
        return WorkRef(
            request_turn.session,
            request.prompt,
            request_turn,
            WorkerRef(
                request_turn.session,
                WorkerKind.SUBAGENT,
                actor.actor_id,
                f"/root/{work_names.worker_name(request.name)}" if harness == CODEX_HARNESS else actor.actor_id,
                request_turn.actor_id,
            ),
            child_turn,
            AssignmentRef(request_turn.session, assignment.assignment_id),
        )

    def _send_lead_turn(self: _WorkResolutionContext, session: SessionRef, prompt: str) -> TurnRef:
        lead = self._client.sessions.snapshot(session).lead()
        receipt = self._client.sessions.send(session, prompt)
        if receipt.status_code != HTTPStatus.OK or receipt.outcome.status not in {"sent", "queued"}:
            msg = f"send action {receipt.request_id!r} was not accepted: {receipt.outcome}"
            raise AssertionError(msg)
        return TurnRef(
            session,
            prompt,
            receipt.cursor_before,
            lead.statistics.prompt_count + 1,
            actor_id=lead.actor_id,
        )


class WorkDriver(WorkResolution):
    """Represent work driver."""

    def __init__(
        self,
        client: BaqylauClient,
        workspace: str,
        wait_policy: WaitPolicy,
    ) -> None:
        """Initialize the object."""
        self._client = client
        self._workspace = workspace
        self._wait_policy = wait_policy

    def launch(self, spec: SessionSpec, request: WorkRequest) -> StartedWork:
        """Launch a session and resolve its requested work.

        Returns:
            The new session and resolved work reference.

        """
        request_prompt = work_delegation.request_prompt(spec.harness, request)
        launch = self._client.sessions.launch(
            SessionLaunchRequest(
                spec.harness,
                workspace=spec.workspace or self._workspace,
                prompt=request_prompt,
                model=spec.model,
                effort=spec.effort,
                attachments=request.attachments,
                account_id=spec.account_id,
            ),
        )
        session = self._client.sessions.wait_for_session(
            launch,
            self._wait_policy.session_announcement,
        )
        request_turn = selector_turns.launched_turn(
            self._client.sessions.watch(session),
            self._wait_policy.feed,
        )
        return StartedWork(
            session,
            self._resolve(
                request_turn,
                spec.harness,
                request,
            ),
        )

    def assign(
        self,
        spec: SessionSpec,
        session: SessionRef,
        request: WorkRequest,
    ) -> WorkRef:
        """Send a work request to an existing session.

        Returns:
            The resolved work reference for the accepted request.

        Raises:
            AssertionError: If the send response is not accepted as sent or queued.

        """
        lead = self._client.sessions.snapshot(session).lead()
        request_prompt = work_delegation.request_prompt(spec.harness, request)
        receipt = self._client.sessions.send(
            session,
            request_prompt,
            attachments=request.attachments,
        )
        if receipt.status_code != HTTPStatus.OK or receipt.outcome.status not in {"sent", "queued"}:
            msg = f"send action {receipt.request_id!r} was not accepted: {receipt.outcome}"
            raise AssertionError(
                msg,
            )
        request_turn = TurnRef(
            session,
            request_prompt,
            receipt.cursor_before,
            lead.statistics.prompt_count + 1,
            actor_id=lead.actor_id,
            attachment_paths=tuple(
                attachment.local_path
                for attachment in request.attachments
                if spec.harness != OPENCODE2_HARNESS and not (
                    spec.harness == CLAUDE_CODE_HARNESS and (attachment.media_type or "").startswith("image/")
                )
            ),
            native_attachment_names=tuple(
                attachment.display_name
                for attachment in request.attachments
                if spec.harness == OPENCODE2_HARNESS
                or (spec.harness == CLAUDE_CODE_HARNESS and (attachment.media_type or "").startswith("image/"))
            ),
        )
        return self._resolve(
            request_turn,
            spec.harness,
            request,
        )

    def launch_parallel(
        self,
        spec: SessionSpec,
        requests: tuple[WorkRequest, ...],
    ) -> StartedParallelWork:
        """Launch one session with parallel work requests.

        Returns:
            The session, request turn, and named work references.

        """
        request_prompt = _parallel_delegation_prompt(spec.harness, requests)
        launch = self._client.sessions.launch(
            SessionLaunchRequest(
                spec.harness,
                workspace=spec.workspace or self._workspace,
                prompt=request_prompt,
                model=spec.model,
                effort=spec.effort,
                account_id=spec.account_id,
            ),
        )
        session = self._client.sessions.wait_for_session(
            launch,
            self._wait_policy.session_announcement,
        )
        request_turn = selector_turns.launched_turn(
            self._client.sessions.watch(session),
            self._wait_policy.feed,
        )
        works = tuple(
            (
                request.name,
                self._resolve(
                    request_turn,
                    spec.harness,
                    WorkRequest(
                        request.name,
                        request.prompt,
                        exact_actor_name=(
                            work_names.assignment_actor_name(spec.harness, request.name)
                            if spec.harness in NAMED_PARALLEL_HARNESSES
                            else None
                        ),
                        exact_prompt=(
                            None if spec.harness in NAMED_PARALLEL_HARNESSES else request.prompt
                        ),
                    ),
                ),
            )
            for request in requests
        )
        return StartedParallelWork(session, request_turn, works)

    def launch_with_followup(
        self,
        spec: SessionSpec,
        *,
        work_name: str,
        prompt: str,
        followup: str,
    ) -> StartedWork:
        """Launch delegated work with a follow-up instruction.

        Returns:
            The new session and initial work reference.

        """
        request_prompt = _delegation_with_followup_prompt(
            spec.harness,
            work_name,
            prompt,
            followup,
        )
        launch = self._client.sessions.launch(
            SessionLaunchRequest(
                spec.harness,
                workspace=spec.workspace or self._workspace,
                prompt=request_prompt,
                model=spec.model,
                effort=spec.effort,
                account_id=spec.account_id,
            ),
        )
        session = self._client.sessions.wait_for_session(
            launch,
            self._wait_policy.session_announcement,
        )
        request_turn = selector_turns.launched_turn(
            self._client.sessions.watch(session),
            self._wait_policy.feed,
        )
        return StartedWork(
            session,
            self._resolve(
                request_turn,
                spec.harness,
                WorkRequest(work_name, prompt),
            ),
        )

    def launch_with_parent_message(
        self,
        spec: SessionSpec,
        *,
        work_name: str,
        message: str,
        result: str,
    ) -> StartedWork:
        """Launch child work that sends a message to the lead actor.

        Returns:
            The new session and child work reference.

        """
        child_prompt = _child_to_lead_message_prompt(spec.harness, message, result)
        request_prompt = work_delegation.delegation_prompt(
            spec.harness,
            WorkRequest(work_name, child_prompt),
        )
        launch = self._client.sessions.launch(
            SessionLaunchRequest(
                spec.harness,
                workspace=spec.workspace or self._workspace,
                prompt=request_prompt,
                model=spec.model,
                effort=spec.effort,
                account_id=spec.account_id,
            ),
        )
        session = self._client.sessions.wait_for_session(
            launch,
            self._wait_policy.session_announcement,
        )
        request_turn = selector_turns.launched_turn(
            self._client.sessions.watch(session),
            self._wait_policy.feed,
        )
        return StartedWork(
            session,
            self._resolve(
                request_turn,
                spec.harness,
                WorkRequest(work_name, child_prompt),
            ),
        )

    def interrupt(self, spec: SessionSpec, work: WorkRef) -> WorkerControlRef:
        """Ask the lead actor to interrupt a named subagent.

        Returns:
            The work and lead turn used to request the interruption.

        Raises:
            AssertionError: If the worker has no subagent address or the harness is unsupported.

        """
        if work.worker.kind != WorkerKind.SUBAGENT or work.worker.address is None:
            message = "worker interruption requires a named subagent"
            raise AssertionError(message)
        worker_address = work.worker.address
        if spec.harness == CODEX_HARNESS:
            prompt = (
                "Use interrupt_agent exactly once. Set target to "
                f"{worker_address!r}. Do not use another tool. After the "
                "interrupt request returns, reply only with INTERRUPT_SENT."
            )
            return WorkerControlRef(work, turn=self._send_lead_turn(work.session, prompt))
        if spec.harness == CLAUDE_CODE_HARNESS:
            prompt = (
                "Use TaskStop exactly once. Set task_id to "
                f"{worker_address!r}. Do not stop another task. After the "
                "stop request returns, reply only with INTERRUPT_SENT."
            )
            return WorkerControlRef(work, turn=self._send_lead_turn(work.session, prompt))
        message = f"harness {spec.harness!r} has no worker interrupt adapter"
        raise AssertionError(message)

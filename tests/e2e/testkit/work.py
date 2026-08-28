"""Start named work on a lead or on one real subagent."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from api.controls.models.attachment_reference import AttachmentReferenceBody
from sdk.client import BaqylauClient, SessionRef
from tests.e2e.testkit import selectors
from tests.e2e.testkit.policy import WaitPolicy
from tests.e2e.testkit.references import (
    AssignmentRef,
    SessionSpec,
    TurnRef,
    WorkerKind,
    WorkerControlRef,
    WorkerRef,
    WorkRef,
)


@dataclass(frozen=True)
class StartedWork:
    session: SessionRef
    work: WorkRef


@dataclass(frozen=True)
class WorkRequest:
    name: str
    prompt: str


@dataclass(frozen=True)
class StartedParallelWork:
    session: SessionRef
    request_turn: TurnRef
    works: tuple[tuple[str, WorkRef], ...]


def _worker_name(work_name: str) -> str:
    words = re.findall(r"[a-z0-9]+", work_name.casefold())
    return "e2e_" + "_".join(words)[:40]


def _assignment_actor_name(harness: str, work_name: str) -> str:
    native_name = _worker_name(work_name)
    return native_name.replace("_", " ") if harness == "codex" else native_name


def _delegation_prompt(
    harness: str,
    work_name: str,
    work_prompt: str,
    attachments: tuple[AttachmentReferenceBody, ...],
    *,
    named: bool = False,
) -> str:
    delegated_prompt = work_prompt
    if attachments:
        paths = "\n".join(f"- {item.local_path}" for item in attachments)
        delegated_prompt = (
            f"{work_prompt}\n\n"
            "The files for this work are at these exact paths. Use only these paths:\n"
            f"{paths}"
        )
    name = _worker_name(work_name)
    if harness == "codex":
        encoded_message = json.dumps(delegated_prompt).replace("$", "\\u0024")
        instruction = (
            "Use spawn_agent exactly once. "
            f"Set task_name to {name!r}. Decode WORK MESSAGE JSON as JSON and "
            "set message to the decoded string exactly. Do not do the work yourself. "
            "Do not use another tool. After the subagent starts, reply only "
            "with the word delegated."
        )
    elif harness == "claude_code":
        name_instruction = (
            f"Set name to {name!r}. "
            if named
            else "Do not set name. "
        )
        instruction = (
            "Use the Agent tool exactly once. "
            f"Use description {name!r}. Give the subagent the exact work text "
            "between WORK START and WORK END. Do not do the work yourself. "
            f"{name_instruction}Do not use another tool. After the Agent tool returns, reply only "
            "with the word delegated."
        )
    else:
        raise AssertionError(f"harness {harness!r} has no subagent work adapter")
    if harness == "codex":
        return (
            f"{instruction}\n\n"
            f"WORK MESSAGE JSON\n{encoded_message}"
        )
    return f"{instruction}\n\nWORK START\n{delegated_prompt}\nWORK END"


def _parallel_delegation_prompt(
    harness: str,
    requests: tuple[WorkRequest, ...],
) -> str:
    if len(requests) < 2:
        raise AssertionError("parallel work requires at least two work items")
    names = [_worker_name(request.name) for request in requests]
    if len(set(names)) != len(names):
        raise AssertionError("parallel work names must have distinct native names")
    if harness == "codex":
        instruction = (
            "Use spawn_agent once for every work item below. "
            "Make all spawn calls in one response so the subagents run in parallel. "
            "For each call, set task_name to the stated worker name and set message "
            "to the exact text between WORK START and WORK END. Do not do the work "
            "yourself. After all subagents start, reply only with the word launched."
        )
    elif harness == "claude_code":
        instruction = (
            "Use the Agent tool once for every work item below. Put all Agent calls "
            "in one response so the subagents run in parallel. For each call, set "
            "description to the stated work name and set prompt to the exact text "
            "between WORK START and WORK END. Do not set name. Do not do the work "
            "yourself. Each Agent call returns an async launch acknowledgement. "
            "Immediately after the final launch acknowledgement, reply only with "
            "the word launched. Do not wait for child completion or notifications."
        )
    else:
        raise AssertionError(f"harness {harness!r} has no subagent work adapter")
    blocks = "\n\n".join(
        (
            f"WORKER NAME: {_worker_name(request.name)}\n"
            if harness == "codex"
            else f"WORK NAME: {request.name}\n"
        )
        + f"WORK START\n{request.prompt}\nWORK END"
        for request in requests
    )
    return f"{instruction}\n\n{blocks}"


def _delegation_with_followup_prompt(
    harness: str,
    work_name: str,
    work_prompt: str,
    followup: str,
) -> str:
    name = _worker_name(work_name)
    if harness == "codex":
        work_json = json.dumps(work_prompt).replace("$", "\\u0024")
        followup_json = json.dumps(followup).replace("$", "\\u0024")
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
    if harness == "claude_code":
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
    raise AssertionError(f"harness {harness!r} has no subagent follow-up adapter")


def _child_to_lead_message_prompt(
    harness: str,
    message: str,
    result: str,
) -> str:
    if harness == "claude_code":
        return (
            "Use SendMessage exactly once. Set `to` to 'team-lead'. Set `message` "
            "to the exact text between PARENT MESSAGE START and PARENT MESSAGE "
            "END. Do not use another tool. After the message request returns, "
            f"reply only with {result}."
            f"\n\nPARENT MESSAGE START\n{message}\nPARENT MESSAGE END"
        )
    raise AssertionError(f"harness {harness!r} has no actor message adapter")


class WorkDriver:
    def __init__(
        self,
        client: BaqylauClient,
        workspace: str,
        wait_policy: WaitPolicy,
    ) -> None:
        self._client = client
        self._workspace = workspace
        self._wait_policy = wait_policy

    def launch(
        self,
        spec: SessionSpec,
        *,
        work_name: str,
        worker_kind: WorkerKind,
        prompt: str,
        attachments: tuple[AttachmentReferenceBody, ...] = (),
    ) -> StartedWork:
        request_prompt = self._request_prompt(
            spec,
            work_name,
            worker_kind,
            prompt,
            attachments,
        )
        launch = self._client.sessions.launch(
            spec.harness,
            workspace=spec.workspace or self._workspace,
            prompt=request_prompt,
            model=spec.model,
            effort=spec.effort,
            attachments=attachments,
            account_id=spec.account_id,
        )
        session = self._client.sessions.wait_for_session(
            launch,
            self._wait_policy.session_announcement,
        )
        request_turn = selectors.launched_turn(
            self._client.sessions.watch(session),
            self._wait_policy.feed,
        )
        return StartedWork(
            session,
            self._resolve(
                request_turn,
                harness=spec.harness,
                work_name=work_name,
                prompt=prompt,
                worker_kind=worker_kind,
            ),
        )

    def assign(
        self,
        spec: SessionSpec,
        session: SessionRef,
        *,
        work_name: str,
        worker_kind: WorkerKind,
        prompt: str,
        attachments: tuple[AttachmentReferenceBody, ...] = (),
        named: bool = False,
    ) -> WorkRef:
        before = self._client.sessions.snapshot(session)
        lead = before.lead()
        request_prompt = self._request_prompt(
            spec,
            work_name,
            worker_kind,
            prompt,
            attachments,
            named=named,
        )
        receipt = self._client.sessions.send(
            session,
            request_prompt,
            attachments=attachments,
        )
        if receipt.status_code != 200 or receipt.outcome.status not in ("sent", "queued"):
            raise AssertionError(
                f"send action {receipt.request_id!r} was not accepted: {receipt.outcome}"
            )
        request_turn = TurnRef(
            session,
            request_prompt,
            receipt.cursor_before,
            lead.statistics.prompt_count + 1,
            actor_id=lead.actor_id,
            attachment_paths=tuple(
                item.local_path
                for item in attachments
                if not (
                    spec.harness == "claude_code"
                    and (item.media_type or "").startswith("image/")
                )
            ),
            native_attachment_names=tuple(
                item.display_name
                for item in attachments
                if spec.harness == "claude_code"
                and (item.media_type or "").startswith("image/")
            ),
        )
        return self._resolve(
            request_turn,
            harness=spec.harness,
            work_name=work_name,
            prompt=prompt,
            worker_kind=worker_kind,
            exact_actor_name=(
                _assignment_actor_name(spec.harness, work_name)
                if named and worker_kind == WorkerKind.SUBAGENT
                else None
            ),
        )

    def launch_parallel(
        self,
        spec: SessionSpec,
        requests: tuple[WorkRequest, ...],
    ) -> StartedParallelWork:
        request_prompt = _parallel_delegation_prompt(spec.harness, requests)
        launch = self._client.sessions.launch(
            spec.harness,
            workspace=spec.workspace or self._workspace,
            prompt=request_prompt,
            model=spec.model,
            effort=spec.effort,
            account_id=spec.account_id,
        )
        session = self._client.sessions.wait_for_session(
            launch,
            self._wait_policy.session_announcement,
        )
        request_turn = selectors.launched_turn(
            self._client.sessions.watch(session),
            self._wait_policy.feed,
        )
        works = tuple(
            (
                request.name,
                self._resolve(
                    request_turn,
                    harness=spec.harness,
                    work_name=request.name,
                    prompt=request.prompt,
                    worker_kind=WorkerKind.SUBAGENT,
                    exact_actor_name=(
                        _assignment_actor_name(spec.harness, request.name)
                        if spec.harness == "codex"
                        else None
                    ),
                    exact_prompt=request.prompt if spec.harness == "claude_code" else None,
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
        request_prompt = _delegation_with_followup_prompt(
            spec.harness,
            work_name,
            prompt,
            followup,
        )
        launch = self._client.sessions.launch(
            spec.harness,
            workspace=spec.workspace or self._workspace,
            prompt=request_prompt,
            model=spec.model,
            effort=spec.effort,
            account_id=spec.account_id,
        )
        session = self._client.sessions.wait_for_session(
            launch,
            self._wait_policy.session_announcement,
        )
        request_turn = selectors.launched_turn(
            self._client.sessions.watch(session),
            self._wait_policy.feed,
        )
        return StartedWork(
            session,
            self._resolve(
                request_turn,
                harness=spec.harness,
                work_name=work_name,
                prompt=prompt,
                worker_kind=WorkerKind.SUBAGENT,
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
        child_prompt = _child_to_lead_message_prompt(spec.harness, message, result)
        request_prompt = _delegation_prompt(
            spec.harness,
            work_name,
            child_prompt,
            (),
        )
        launch = self._client.sessions.launch(
            spec.harness,
            workspace=spec.workspace or self._workspace,
            prompt=request_prompt,
            model=spec.model,
            effort=spec.effort,
            account_id=spec.account_id,
        )
        session = self._client.sessions.wait_for_session(
            launch,
            self._wait_policy.session_announcement,
        )
        request_turn = selectors.launched_turn(
            self._client.sessions.watch(session),
            self._wait_policy.feed,
        )
        return StartedWork(
            session,
            self._resolve(
                request_turn,
                harness=spec.harness,
                work_name=work_name,
                prompt=child_prompt,
                worker_kind=WorkerKind.SUBAGENT,
            ),
        )

    @staticmethod
    def _request_prompt(
        spec: SessionSpec,
        work_name: str,
        worker_kind: WorkerKind,
        prompt: str,
        attachments: tuple[AttachmentReferenceBody, ...],
        *,
        named: bool = False,
    ) -> str:
        if worker_kind == WorkerKind.LEAD:
            return prompt
        return _delegation_prompt(
            spec.harness,
            work_name,
            prompt,
            attachments,
            named=named,
        )

    def _resolve(
        self,
        request_turn: TurnRef,
        *,
        harness: str,
        work_name: str,
        prompt: str,
        worker_kind: WorkerKind,
        exact_actor_name: str | None = None,
        exact_prompt: str | None = None,
    ) -> WorkRef:
        watch = self._client.sessions.watch(request_turn.session)
        request_turn = selectors.turn(
            watch,
            request_turn,
            self._wait_policy.turn,
        )
        if request_turn.actor_id is None:
            raise AssertionError("work request does not have a lead actor")
        if worker_kind == WorkerKind.LEAD:
            worker = WorkerRef(
                request_turn.session,
                WorkerKind.LEAD,
                request_turn.actor_id,
            )
            return WorkRef(
                request_turn.session,
                prompt,
                request_turn,
                worker,
                request_turn,
            )

        assignment = selectors.assignment(
            watch,
            turn_reference=request_turn,
            exact_actor_name=exact_actor_name,
            exact_prompt=exact_prompt,
            timeout=self._wait_policy.turn,
        )
        actor = selectors.actor_from_assignment(
            watch,
            assignment_reference=assignment,
            timeout=self._wait_policy.background,
        )
        child_turn = selectors.actor_assignment_turn(
            watch,
            actor_reference=actor,
            assignment_reference=assignment,
            requested_prompt=prompt,
            timeout=self._wait_policy.turn,
        )
        return WorkRef(
            request_turn.session,
            prompt,
            request_turn,
            WorkerRef(
                request_turn.session,
                WorkerKind.SUBAGENT,
                actor.actor_id,
                (
                    f"/root/{_worker_name(work_name)}"
                    if harness == "codex"
                    else actor.actor_id
                ),
                request_turn.actor_id,
            ),
            child_turn,
            AssignmentRef(request_turn.session, assignment.assignment_id),
        )

    def interrupt(self, spec: SessionSpec, work: WorkRef) -> WorkerControlRef:
        if work.worker.kind != WorkerKind.SUBAGENT or work.worker.address is None:
            raise AssertionError("worker interruption requires a named subagent")
        if spec.harness == "codex":
            prompt = (
                "Use interrupt_agent exactly once. Set target to "
                f"{work.worker.address!r}. Do not use another tool. After the "
                "interrupt request returns, reply only with INTERRUPT_SENT."
            )
            return WorkerControlRef(work, turn=self._send_lead_turn(work.session, prompt))
        raise AssertionError(f"harness {spec.harness!r} has no worker interrupt adapter")

    def _send_lead_turn(self, session: SessionRef, prompt: str) -> TurnRef:
        before = self._client.sessions.snapshot(session)
        lead = before.lead()
        receipt = self._client.sessions.send(session, prompt)
        if receipt.status_code != 200 or receipt.outcome.status not in ("sent", "queued"):
            raise AssertionError(
                f"send action {receipt.request_id!r} was not accepted: {receipt.outcome}"
            )
        return TurnRef(
            session,
            prompt,
            receipt.cursor_before,
            lead.statistics.prompt_count + 1,
            actor_id=lead.actor_id,
        )

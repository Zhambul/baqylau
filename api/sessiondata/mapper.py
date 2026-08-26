# Read model -> the HTTP boundary. The api layer's own decision about what is exposed.
#
# Two things happen here that happen nowhere else. The writers' internal memory
# — the pending-attention set, the path set, the four title candidates — is
# DROPPED: it exists so a restart resumes the fold, and no client has any use
# for it. And `active_seconds` gains the interval still open, measured against
# now, because the stored number is only the closed ones (a number that grows on
# its own cannot be a stored fact).
#
# `active` is the one thing derived from that internal memory rather than hidden
# with it, and it is derived rather than exposed: a client is told THAT an
# interval is open, which is what lets it carry the clock forward between frames,
# and not WHEN it opened, which is a writer's resume detail.
from __future__ import annotations

import time

from api.common.mapper import values
from api.sessiondata.models.entry import (
    AssignmentFinishedBodyResponse,
    AssignmentStartedBodyResponse,
    CompactionFinishedBodyResponse,
    CompactionStartedBodyResponse,
    EffortChangeBodyResponse,
    EntryBodyResponse,
    EntryPageResponse,
    EntryResponse,
    FileBodyResponse,
    MessageBodyResponse,
    ModelChangeBodyResponse,
    PlanProposedBodyResponse,
    PlanResolvedBodyResponse,
    QuestionAnswerResponse,
    QuestionAnsweredBodyResponse,
    QuestionAskedBodyResponse,
    QuestionChoiceResponse,
    QuestionResponse,
    ReasoningBodyResponse,
    SearchBodyResponse,
    ShellBackgroundedBodyResponse,
    ShellFinishedBodyResponse,
    ShellOutputBodyResponse,
    ShellStartedBodyResponse,
    SkillFinishedBodyResponse,
    SkillStartedBodyResponse,
    TurnFinishedBodyResponse,
    TurnStartedBodyResponse,
    WebBodyResponse,
    WorktreeBodyResponse,
)
from api.sessiondata.models.session_data import (
    ActorBackgroundResponse,
    ActorContextResponse,
    ActorResponse,
    ActorStatisticsResponse,
    ActorUsageResponse,
    GoalResponse,
    SessionDataResponse,
    SessionResponse,
    TaskResponse,
    ToolCountResponse,
)
from core.repository import RepositoryStatus
from domain import entries as bodies
from domain.entries import EntryBody, SessionEntry
from domain.sessiondata import ActorFacts, SessionData, SessionFacts
from repository.contract.session_data import EntryPage


def session_data(
    session_data: SessionData,
    *,
    live: bool,
    repository_status: RepositoryStatus | None,
    project_directory: str | None = None,
    now: float | None = None,
) -> SessionDataResponse:
    return SessionDataResponse(
        cursor=session_data.cursor,
        session=session(session_data.session),
        actors=tuple(actor(row, now=now) for row in session_data.actors),
        live=live,
        project_directory=(
            project_directory or session_data.session.working_directory
        ),
        repository=values.maybe_repository_status(repository_status),
    )


def session(session_facts: SessionFacts) -> SessionResponse:
    return SessionResponse(
        session_id=str(session_facts.session_id),
        harness=session_facts.harness,
        title=session_facts.title,
        state=session_facts.state,
        working_directory=session_facts.working_directory,
        started_at=session_facts.started_at,
        finished_at=session_facts.finished_at,
        account=values.maybe_account_reference(session_facts.account),
        lead_actor_id=str(session_facts.lead_actor_id),
        continued_from=(
            None if session_facts.continued_from is None else str(session_facts.continued_from)
        ),
        goal=(
            None
            if session_facts.goal is None
            else GoalResponse(
                objective=session_facts.goal.objective,
                state=session_facts.goal.state,
                reason=session_facts.goal.reason,
                completed=session_facts.goal.state == "completed",
            )
        ),
        tasks=tuple(
            TaskResponse(
                task_id=str(task.task_id),
                subject=task.subject,
                description=task.description,
                state=task.state,
                owner_actor_id=None if task.owner_actor_id is None else str(task.owner_actor_id),
            )
            for task in session_facts.tasks
        ),
    )


def actor(actor_facts: ActorFacts, *, now: float | None = None) -> ActorResponse:
    statistics = actor_facts.statistics
    open_interval = (
        0.0
        if statistics.active_since_internal is None
        else max(0.0, (now if now is not None else time.time()) - statistics.active_since_internal)
    )
    return ActorResponse(
        session_id=str(actor_facts.session_id),
        actor_id=str(actor_facts.actor_id),
        parent_actor_id=None if actor_facts.parent_actor_id is None else str(actor_facts.parent_actor_id),
        role=actor_facts.role,
        name=actor_facts.name,
        description=actor_facts.description,
        state=actor_facts.state,
        started_at=actor_facts.started_at,
        finished_at=actor_facts.finished_at,
        # One display string, which is all a model is to a reader; the picker
        # gets its selectable ids from the harness catalog, not from here.
        model=None if actor_facts.model is None else (
            actor_facts.model.display_name or actor_facts.model.name
        ),
        effort=actor_facts.effort,
        status=actor_facts.status,
        usage=ActorUsageResponse(
            tokens=values.token_usage(actor_facts.usage.tokens),
            # A string, because a decimal is money and a float is not: JSON has
            # one number type and it rounds.
            cost_in_usd=None if actor_facts.usage.cost_in_usd is None else str(actor_facts.usage.cost_in_usd),
        ),
        context=ActorContextResponse(
            used_tokens=actor_facts.context.used_tokens,
            window_tokens=actor_facts.context.window_tokens,
            compacting=actor_facts.context.compacting,
        ),
        background=ActorBackgroundResponse(
            running_shell_ids=tuple(str(shell_id) for shell_id in actor_facts.background.running_shell_ids),
            monitor_count=actor_facts.background.monitor_count,
            background_job_count=actor_facts.background.background_job_count,
        ),
        statistics=ActorStatisticsResponse(
            prompt_count=statistics.prompt_count,
            shell_command_count=statistics.shell_command_count,
            failed_shell_command_count=statistics.failed_shell_command_count,
            file_count=statistics.file_count,
            lines_added=statistics.lines_added,
            lines_removed=statistics.lines_removed,
            actor_message_count=statistics.actor_message_count,
            tool_counts=tuple(
                ToolCountResponse(tool=tool, count=count) for tool, count in statistics.tool_counts
            ),
            active_seconds=statistics.active_seconds + open_interval,
            active=statistics.active_since_internal is not None,
        ),
    )


def entry_page(entry_page: EntryPage) -> EntryPageResponse:
    return EntryPageResponse(
        items=tuple(entry(item) for item in entry_page.items),
        oldest_cursor=entry_page.oldest_cursor,
        has_more=entry_page.has_more,
    )


def entry(session_entry: SessionEntry) -> EntryResponse:
    return EntryResponse(
        entry_id=str(session_entry.entry_id),
        type=session_entry.entry_type,
        cursor=session_entry.cursor,
        actor_id=str(session_entry.actor_id),
        parent_actor_id=None if session_entry.parent_actor_id is None else str(session_entry.parent_actor_id),
        turn_id=None if session_entry.turn_id is None else str(session_entry.turn_id),
        occurred_at=session_entry.occurred_at,
        summary=session_entry.summary,
        body=entry_body(session_entry.body),
    )


def entry_body(entry_body: EntryBody) -> EntryBodyResponse:
    """One mapping per kind, and the exhaustiveness is the point: a body with no
    mapping is a kind nobody decided how to draw, and it fails here rather than
    reaching a client as an empty object."""
    if isinstance(entry_body, bodies.TurnStartedBody):
        return TurnStartedBodyResponse()
    if isinstance(entry_body, bodies.TurnFinishedBody):
        return TurnFinishedBodyResponse(state=entry_body.state)
    if isinstance(entry_body, bodies.MessageBody):
        return MessageBodyResponse(
            message_id=str(entry_body.message_id),
            role=entry_body.role,
            phase=entry_body.phase,
            content=values.content(entry_body.content),
            recipient_actor_id=(
                None if entry_body.recipient_actor_id is None else str(entry_body.recipient_actor_id)
            ),
            reply_to=None if entry_body.reply_to is None else str(entry_body.reply_to),
        )
    if isinstance(entry_body, bodies.ReasoningBody):
        return ReasoningBodyResponse(
            reasoning_id=entry_body.reasoning_id, content=values.content(entry_body.content)
        )
    if isinstance(entry_body, bodies.ShellStartedBody):
        return ShellStartedBodyResponse(
            shell_id=str(entry_body.shell_id),
            command=values.content(entry_body.command),
            execution=entry_body.execution,
        )
    if isinstance(entry_body, bodies.ShellOutputBody):
        return ShellOutputBodyResponse(
            shell_id=str(entry_body.shell_id),
            stream=entry_body.stream,
            mode=entry_body.mode,
            content=values.content(entry_body.content),
        )
    if isinstance(entry_body, bodies.ShellBackgroundedBody):
        return ShellBackgroundedBodyResponse(shell_id=str(entry_body.shell_id))
    if isinstance(entry_body, bodies.ShellFinishedBody):
        return ShellFinishedBodyResponse(
            shell_id=str(entry_body.shell_id),
            state=entry_body.state,
            exit_code=entry_body.exit_code,
            result=values.maybe_content(entry_body.result),
        )
    if isinstance(entry_body, bodies.FileBody):
        return FileBodyResponse(
            path=entry_body.path,
            action=entry_body.action,
            state=entry_body.state,
            previous_path=entry_body.previous_path,
            lines_added=entry_body.lines_added,
            lines_removed=entry_body.lines_removed,
            content=values.maybe_content(entry_body.content),
        )
    if isinstance(entry_body, bodies.SearchBody):
        return SearchBodyResponse(
            tool=entry_body.tool,
            query=values.content(entry_body.query),
            state=entry_body.state,
            result=values.maybe_content(entry_body.result),
        )
    if isinstance(entry_body, bodies.WebBody):
        return WebBodyResponse(
            url=entry_body.url, state=entry_body.state, result=values.maybe_content(entry_body.result)
        )
    if isinstance(entry_body, bodies.WorktreeBody):
        return WorktreeBodyResponse(
            action=entry_body.action,
            state=entry_body.state,
            arguments=values.maybe_content(entry_body.arguments),
        )
    if isinstance(entry_body, bodies.SkillStartedBody):
        return SkillStartedBodyResponse(
            skill_id=str(entry_body.skill_id),
            name=entry_body.name,
            arguments=values.maybe_content(entry_body.arguments),
        )
    if isinstance(entry_body, bodies.SkillFinishedBody):
        return SkillFinishedBodyResponse(
            skill_id=str(entry_body.skill_id),
            state=entry_body.state,
            result=values.maybe_content(entry_body.result),
        )
    if isinstance(entry_body, bodies.QuestionAskedBody):
        return QuestionAskedBodyResponse(
            attention_id=str(entry_body.attention_id),
            questions=tuple(
                QuestionResponse(
                    question_id=question.prompt_id,
                    title=question.title,
                    question=question.prompt,
                    multiple=question.multiple,
                    choices=tuple(
                        QuestionChoiceResponse(
                            label=choice.label, description=choice.description
                        )
                        for choice in question.choices
                    ),
                )
                for question in entry_body.questions
            ),
        )
    if isinstance(entry_body, bodies.QuestionAnsweredBody):
        return QuestionAnsweredBodyResponse(
            attention_id=str(entry_body.attention_id),
            answers=tuple(
                QuestionAnswerResponse(question_id=answer.prompt_id, labels=answer.labels)
                for answer in entry_body.answers
            ),
            feedback=entry_body.feedback,
        )
    if isinstance(entry_body, bodies.PlanProposedBody):
        return PlanProposedBodyResponse(
            attention_id=str(entry_body.attention_id), plan=values.content(entry_body.plan)
        )
    if isinstance(entry_body, bodies.PlanResolvedBody):
        return PlanResolvedBodyResponse(
            attention_id=str(entry_body.attention_id),
            state=entry_body.state,
            feedback=entry_body.feedback,
            edited=entry_body.edited,
        )
    if isinstance(entry_body, bodies.CompactionStartedBody):
        return CompactionStartedBodyResponse(before_tokens=entry_body.before_tokens)
    if isinstance(entry_body, bodies.CompactionFinishedBody):
        return CompactionFinishedBodyResponse(
            before_tokens=entry_body.before_tokens,
            after_tokens=entry_body.after_tokens,
            context=values.maybe_content(entry_body.context),
        )
    if isinstance(entry_body, bodies.AssignmentStartedBody):
        return AssignmentStartedBodyResponse(
            assignment_id=str(entry_body.assignment_id),
            assigned_actor_name=entry_body.assigned_actor_name,
            prompt=values.maybe_content(entry_body.prompt),
        )
    if isinstance(entry_body, bodies.AssignmentFinishedBody):
        return AssignmentFinishedBodyResponse(
            assignment_id=str(entry_body.assignment_id),
            state=entry_body.state,
            result=values.maybe_content(entry_body.result),
        )
    if isinstance(entry_body, bodies.ModelChangeBody):
        return ModelChangeBodyResponse(
            current=entry_body.current, previous=entry_body.previous, automatic=entry_body.automatic
        )
    if isinstance(entry_body, bodies.EffortChangeBody):
        return EffortChangeBodyResponse(current=entry_body.current, previous=entry_body.previous)
    raise TypeError(f"unmapped entry body: {type(entry_body).__name__}")

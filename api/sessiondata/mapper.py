# Read model -> wire. The api layer's own decision about what is exposed.
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
    data: SessionData,
    *,
    live: bool,
    repository: RepositoryStatus | None,
    now: float | None = None,
) -> SessionDataResponse:
    return SessionDataResponse(
        cursor=data.cursor,
        session=session(data.session),
        actors=tuple(actor(row, now=now) for row in data.actors),
        live=live,
        repository=values.maybe_repository_status(repository),
    )


def session(facts: SessionFacts) -> SessionResponse:
    return SessionResponse(
        session_id=str(facts.session_id),
        harness=facts.harness,
        title=facts.title,
        state=facts.state,
        working_directory=facts.working_directory,
        started_at=facts.started_at,
        finished_at=facts.finished_at,
        account=values.maybe_account_reference(facts.account),
        lead_actor_id=str(facts.lead_actor_id),
        goal=(
            None
            if facts.goal is None
            else GoalResponse(objective=facts.goal.objective, completed=facts.goal.completed)
        ),
        tasks=tuple(
            TaskResponse(
                task_id=str(task.task_id),
                subject=task.subject,
                description=task.description,
                state=task.state,
                owner_actor_id=None if task.owner_actor_id is None else str(task.owner_actor_id),
            )
            for task in facts.tasks
        ),
    )


def actor(facts: ActorFacts, *, now: float | None = None) -> ActorResponse:
    statistics = facts.statistics
    open_interval = (
        0.0
        if statistics.active_since_internal is None
        else max(0.0, (now if now is not None else time.time()) - statistics.active_since_internal)
    )
    return ActorResponse(
        session_id=str(facts.session_id),
        actor_id=str(facts.actor_id),
        parent_actor_id=None if facts.parent_actor_id is None else str(facts.parent_actor_id),
        role=facts.role,
        name=facts.name,
        description=facts.description,
        state=facts.state,
        started_at=facts.started_at,
        finished_at=facts.finished_at,
        # One display string, which is all a model is to a reader; the picker
        # gets its selectable ids from the harness catalog, not from here.
        model=None if facts.model is None else (
            facts.model.display_name or facts.model.native_id
        ),
        effort=facts.effort,
        status=facts.status,
        usage=ActorUsageResponse(
            tokens=values.token_usage(facts.usage.tokens),
            # A string, because a decimal is money and a float is not: JSON has
            # one number type and it rounds.
            cost_in_usd=None if facts.usage.cost_in_usd is None else str(facts.usage.cost_in_usd),
        ),
        context=ActorContextResponse(
            used_tokens=facts.context.used_tokens,
            window_tokens=facts.context.window_tokens,
            compacting=facts.context.compacting,
        ),
        background=ActorBackgroundResponse(
            running_shell_ids=tuple(str(shell_id) for shell_id in facts.background.running_shell_ids),
            monitor_count=facts.background.monitor_count,
            background_job_count=facts.background.background_job_count,
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


def entry_page(page: EntryPage) -> EntryPageResponse:
    return EntryPageResponse(
        items=tuple(entry(item) for item in page.items),
        oldest_cursor=page.oldest_cursor,
        has_more=page.has_more,
    )


def entry(item: SessionEntry) -> EntryResponse:
    return EntryResponse(
        entry_id=str(item.entry_id),
        type=item.entry_type,
        cursor=item.cursor,
        actor_id=str(item.actor_id),
        parent_actor_id=None if item.parent_actor_id is None else str(item.parent_actor_id),
        turn_id=None if item.turn_id is None else str(item.turn_id),
        occurred_at=item.occurred_at,
        summary=item.summary,
        body=entry_body(item.body),
    )


def entry_body(body: EntryBody) -> EntryBodyResponse:
    """One mapping per kind, and the exhaustiveness is the point: a body with no
    mapping is a kind nobody decided how to draw, and it fails here rather than
    reaching a client as an empty object."""
    if isinstance(body, bodies.TurnStartedBody):
        return TurnStartedBodyResponse()
    if isinstance(body, bodies.TurnFinishedBody):
        return TurnFinishedBodyResponse(state=body.state)
    if isinstance(body, bodies.MessageBody):
        return MessageBodyResponse(
            message_id=str(body.message_id),
            role=body.role,
            phase=body.phase,
            content=values.content(body.content),
            recipient_actor_id=(
                None if body.recipient_actor_id is None else str(body.recipient_actor_id)
            ),
            reply_to=None if body.reply_to is None else str(body.reply_to),
        )
    if isinstance(body, bodies.ReasoningBody):
        return ReasoningBodyResponse(
            reasoning_id=body.reasoning_id, content=values.content(body.content)
        )
    if isinstance(body, bodies.ShellStartedBody):
        return ShellStartedBodyResponse(
            shell_id=str(body.shell_id),
            command=values.content(body.command),
            execution=body.execution,
        )
    if isinstance(body, bodies.ShellOutputBody):
        return ShellOutputBodyResponse(
            shell_id=str(body.shell_id),
            stream=body.stream,
            mode=body.mode,
            content=values.content(body.content),
        )
    if isinstance(body, bodies.ShellBackgroundedBody):
        return ShellBackgroundedBodyResponse(shell_id=str(body.shell_id))
    if isinstance(body, bodies.ShellFinishedBody):
        return ShellFinishedBodyResponse(
            shell_id=str(body.shell_id),
            state=body.state,
            exit_code=body.exit_code,
            result=values.maybe_content(body.result),
        )
    if isinstance(body, bodies.FileBody):
        return FileBodyResponse(
            path=body.path,
            action=body.action,
            state=body.state,
            previous_path=body.previous_path,
            lines_added=body.lines_added,
            lines_removed=body.lines_removed,
            content=values.maybe_content(body.content),
        )
    if isinstance(body, bodies.SearchBody):
        return SearchBodyResponse(
            tool=body.tool,
            query=values.content(body.query),
            state=body.state,
            result=values.maybe_content(body.result),
        )
    if isinstance(body, bodies.WebBody):
        return WebBodyResponse(
            url=body.url, state=body.state, result=values.maybe_content(body.result)
        )
    if isinstance(body, bodies.WorktreeBody):
        return WorktreeBodyResponse(
            action=body.action,
            state=body.state,
            arguments=values.maybe_content(body.arguments),
        )
    if isinstance(body, bodies.SkillStartedBody):
        return SkillStartedBodyResponse(
            skill_id=str(body.skill_id),
            name=body.name,
            arguments=values.maybe_content(body.arguments),
        )
    if isinstance(body, bodies.SkillFinishedBody):
        return SkillFinishedBodyResponse(
            skill_id=str(body.skill_id),
            state=body.state,
            result=values.maybe_content(body.result),
        )
    if isinstance(body, bodies.QuestionAskedBody):
        return QuestionAskedBodyResponse(
            attention_id=str(body.attention_id),
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
                for question in body.questions
            ),
        )
    if isinstance(body, bodies.QuestionAnsweredBody):
        return QuestionAnsweredBodyResponse(
            attention_id=str(body.attention_id),
            answers=tuple(
                QuestionAnswerResponse(question_id=answer.prompt_id, labels=answer.labels)
                for answer in body.answers
            ),
            feedback=body.feedback,
        )
    if isinstance(body, bodies.PlanProposedBody):
        return PlanProposedBodyResponse(
            attention_id=str(body.attention_id), plan=values.content(body.plan)
        )
    if isinstance(body, bodies.PlanResolvedBody):
        return PlanResolvedBodyResponse(
            attention_id=str(body.attention_id),
            state=body.state,
            feedback=body.feedback,
            edited=body.edited,
        )
    if isinstance(body, bodies.CompactionStartedBody):
        return CompactionStartedBodyResponse(before_tokens=body.before_tokens)
    if isinstance(body, bodies.CompactionFinishedBody):
        return CompactionFinishedBodyResponse(
            before_tokens=body.before_tokens, after_tokens=body.after_tokens
        )
    if isinstance(body, bodies.AssignmentStartedBody):
        return AssignmentStartedBodyResponse(
            assignment_id=str(body.assignment_id),
            assigned_actor_name=body.assigned_actor_name,
            prompt=values.maybe_content(body.prompt),
        )
    if isinstance(body, bodies.AssignmentFinishedBody):
        return AssignmentFinishedBodyResponse(
            assignment_id=str(body.assignment_id),
            state=body.state,
            result=values.maybe_content(body.result),
        )
    if isinstance(body, bodies.ModelChangeBody):
        return ModelChangeBodyResponse(
            current=body.current, previous=body.previous, automatic=body.automatic
        )
    if isinstance(body, bodies.EffortChangeBody):
        return EffortChangeBodyResponse(current=body.current, previous=body.previous)
    raise TypeError(f"unmapped entry body: {type(body).__name__}")

"""Domain and harness value objects to the api's own.

Pure functions: no I/O, no service, no request. `maybe_*` is the nullable form,
because `x if x is None else f(x)` at eleven call sites is eleven chances to
get the polarity backwards.
"""

from __future__ import annotations

from api.common.models.values.account_reference import AccountReferenceResponse
from api.common.models.values.content import ContentResponse
from api.common.models.values.model_reference import ModelReferenceResponse
from api.common.models.values.plan_choice import PlanChoiceResponse
from api.common.models.values.repository_status import RepositoryStatusResponse
from api.common.models.values.terminal_state import (
    TerminalInputStateResponse,
    TerminalStateResponse,
)
from api.common.models.values.token_usage import TokenUsageResponse
from api.common.models.values.usage_row import (
    UsageBlockResponse,
    UsageRowResponse,
    UsageWindowResponse,
)
from core.repository import RepositoryStatus
from domain.values import (
    AccountReference,
    Content,
    MediaType,
    ModelReference,
    StructuredContent,
    TokenUsage,
    content_text,
)
from harness.models import PlanChoice, TerminalSessionState, UsageRow


def token_usage(token_usage: TokenUsage) -> TokenUsageResponse:
    return TokenUsageResponse(
        input_tokens=token_usage.input_tokens,
        output_tokens=token_usage.output_tokens,
        cache_read_tokens=token_usage.cache_read_tokens,
        cache_write_tokens=token_usage.cache_write_tokens,
        one_hour_cache_write_tokens=token_usage.one_hour_cache_write_tokens,
    )


def model_reference(model_reference: ModelReference) -> ModelReferenceResponse:
    return ModelReferenceResponse(
        native_id=model_reference.native_id,
        display_name=model_reference.display_name,
        selection_id=model_reference.selection_id,
    )


def maybe_model_reference(
    candidate_model_reference: ModelReference | None,
) -> ModelReferenceResponse | None:
    return (
        model_reference(candidate_model_reference)
        if candidate_model_reference is not None
        else None
    )


def maybe_account_reference(
    account_reference: AccountReference | None,
) -> AccountReferenceResponse | None:
    if account_reference is None:
        return None
    return AccountReferenceResponse(
        account_id=account_reference.account_id, display_name=account_reference.display_name
    )


def content(value: Content) -> ContentResponse:
    """Text and how to draw it. A structured document — a tool's own arguments
    or answer, in a shape we do not define — is laid out as the plain text a
    person reads, which is the only thing a client can do with it."""
    if isinstance(value, StructuredContent):
        return ContentResponse(text=content_text(value), media_type=MediaType.TEXT_PLAIN)
    return ContentResponse(text=value.text, media_type=value.media_type)


def maybe_content(value: Content | None) -> ContentResponse | None:
    return None if value is None else content(value)


def plan_choice(plan_choice: PlanChoice) -> PlanChoiceResponse:
    return PlanChoiceResponse(
        digit=plan_choice.digit, label=plan_choice.label, feedback=plan_choice.feedback
    )


def terminal_state(terminal_session_state: TerminalSessionState) -> TerminalStateResponse:
    return TerminalStateResponse(
        window_id=terminal_session_state.window_id,
        input_state=(
            None if terminal_session_state.input_state is None
            else TerminalInputStateResponse(
                typed_text=terminal_session_state.input_state.typed_text,
                suggestion=terminal_session_state.input_state.suggestion,
            )
        ),
    )


def maybe_repository_status(
    repository_status: RepositoryStatus | None,
) -> RepositoryStatusResponse | None:
    if repository_status is None:
        return None
    return RepositoryStatusResponse(
        branch=repository_status.branch, worktree=repository_status.worktree, dirty=repository_status.dirty
    )


def usage_row(usage_row: UsageRow) -> UsageRowResponse:
    return UsageRowResponse(
        harness=usage_row.harness,
        account_id=usage_row.account_id,
        display_name=usage_row.display_name,
        switchable=usage_row.switchable,
        plan=usage_row.plan,
        windows=tuple(
            UsageWindowResponse(
                key=window.key,
                label=window.label,
                used_percent=window.used_percent,
                resets_at=window.resets_at,
                duration_minutes=window.duration_minutes,
                scope=window.scope,
                model_id=window.model_id,
            )
            for window in usage_row.windows
        ),
        scheduling_score=usage_row.scheduling_score,
        scheduling_allowed=usage_row.scheduling_allowed,
        limit=(
            None if usage_row.limit is None
            else UsageBlockResponse(
                model_id=usage_row.limit.model_id,
                message=usage_row.limit.message,
                resets_at=usage_row.limit.resets_at,
            )
        ),
        authentication_error=usage_row.authentication_error,
    )

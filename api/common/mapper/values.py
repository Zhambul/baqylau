"""Domain and harness value objects to the api's own.

Pure functions: no I/O, no service, no request. `maybe_*` is the nullable form,
because `x if x is None else f(x)` at eleven call sites is eleven chances to
get the polarity backwards.
"""

from __future__ import annotations

from api.common.models.values.account_reference import AccountReferenceResponse
from api.common.models.values.attention_choice import AttentionChoiceResponse
from api.common.models.values.model_reference import ModelReferenceResponse
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
from domain.values import AccountReference, AttentionChoice, ModelReference, TokenUsage
from harness.models import TerminalSessionState, UsageRow


def token_usage(usage: TokenUsage) -> TokenUsageResponse:
    return TokenUsageResponse(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        one_hour_cache_write_tokens=usage.one_hour_cache_write_tokens,
    )


def model_reference(model: ModelReference) -> ModelReferenceResponse:
    return ModelReferenceResponse(
        native_id=model.native_id,
        display_name=model.display_name,
        selection_id=model.selection_id,
    )


def maybe_model_reference(model: ModelReference | None) -> ModelReferenceResponse | None:
    return model_reference(model) if model is not None else None


def maybe_account_reference(
    account: AccountReference | None,
) -> AccountReferenceResponse | None:
    if account is None:
        return None
    return AccountReferenceResponse(
        account_id=account.account_id, display_name=account.display_name
    )


def attention_choice(choice: AttentionChoice) -> AttentionChoiceResponse:
    return AttentionChoiceResponse(
        value=choice.value, label=choice.label, description=choice.description
    )


def terminal_state(state: TerminalSessionState) -> TerminalStateResponse:
    return TerminalStateResponse(
        window_id=state.window_id,
        input_state=(
            None if state.input_state is None
            else TerminalInputStateResponse(
                typed_text=state.input_state.typed_text,
                suggestion=state.input_state.suggestion,
            )
        ),
    )


def maybe_repository_status(
    status: RepositoryStatus | None,
) -> RepositoryStatusResponse | None:
    if status is None:
        return None
    return RepositoryStatusResponse(
        branch=status.branch, worktree=status.worktree, dirty=status.dirty
    )


def usage_row(row: UsageRow) -> UsageRowResponse:
    return UsageRowResponse(
        harness=row.harness,
        account_id=row.account_id,
        display_name=row.display_name,
        switchable=row.switchable,
        plan=row.plan,
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
            for window in row.windows
        ),
        scheduling_score=row.scheduling_score,
        scheduling_allowed=row.scheduling_allowed,
        limit=(
            None if row.limit is None
            else UsageBlockResponse(
                model_id=row.limit.model_id,
                message=row.limit.message,
                resets_at=row.limit.resets_at,
            )
        ),
        authentication_error=row.authentication_error,
    )

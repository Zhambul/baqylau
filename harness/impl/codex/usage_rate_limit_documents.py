# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate Codex rate-limit response documents."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

FOREIGN_DOCUMENT = ConfigDict(extra="ignore", frozen=True, validate_by_name=True)


class RateLimitWindowResult(BaseModel):
    """Represent one native rate-limit window."""

    model_config = FOREIGN_DOCUMENT
    used_percent: Annotated[float | int | None, Field(alias="usedPercent")] = None
    window_duration_mins: Annotated[float | int | None, Field(alias="windowDurationMins")] = None
    resets_at: Annotated[float | int | None, Field(alias="resetsAt")] = None


class RateLimitCredits(BaseModel):
    """Represent native credit information."""

    model_config = FOREIGN_DOCUMENT
    has_credits: Annotated[bool, Field(alias="hasCredits")]
    unlimited: bool
    # Codex 0.156.1 sends null when the account has no credits.
    balance: str | None = None


class RateLimitsResult(BaseModel):
    """Represent the native rate-limit result."""

    model_config = FOREIGN_DOCUMENT
    limit_id: Annotated[str | None, Field(alias="limitId")] = None
    limit_name: Annotated[str | None, Field(alias="limitName")] = None
    primary: RateLimitWindowResult | None = None
    secondary: RateLimitWindowResult | None = None
    credits: RateLimitCredits | None = None
    individual_limit: Annotated[None, Field(alias="individualLimit")] = None
    spend_control_reached: Annotated[bool | None, Field(alias="spendControlReached")] = None
    plan_type: Annotated[str | None, Field(alias="planType")] = None
    rate_limit_reached_type: Annotated[str | None, Field(alias="rateLimitReachedType")] = None


class AccountRateLimitsResponse(BaseModel):
    """Represent the native account rate limits."""

    model_config = FOREIGN_DOCUMENT
    rate_limits: Annotated[RateLimitsResult | None, Field(alias="rateLimits")] = None

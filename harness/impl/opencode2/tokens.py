# Copyright (c) 2026 Zhambyl Yermagambet
"""Read native OpenCode2 token counters."""

from pydantic import BaseModel, ConfigDict, Field


class CacheTokens(BaseModel):
    """Read cache token counts."""

    model_config = ConfigDict(extra="ignore")
    read: int = 0
    write: int = 0


class NativeTokens(BaseModel):
    """Read token counts for one native model step."""

    model_config = ConfigDict(extra="ignore")
    input: int = 0
    output: int = 0
    reasoning: int = 0
    cache: CacheTokens = Field(default_factory=CacheTokens)

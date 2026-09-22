# Copyright (c) 2026 Zhambyl Yermagambet
"""Preserve nonnegative token counts in the public API."""

from baqylau_extension_api.core.base import CoreModel
from baqylau_extension_api.models.base import Revision


class TokenUsage(CoreModel):
    """Keep every token count used by the private core model."""

    input_tokens: Revision = 0
    output_tokens: Revision = 0
    cache_read_tokens: Revision = 0
    cache_write_tokens: Revision = 0
    one_hour_cache_write_tokens: Revision = 0

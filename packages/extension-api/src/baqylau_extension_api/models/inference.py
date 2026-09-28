# Copyright (c) 2026 Zhambyl Yermagambet
"""Send one prompt to the user's configured model of a size class."""

from typing import Annotated, Literal

from pydantic import Field

from baqylau_extension_api.models.base import WireModel

MAX_PROMPT_LENGTH = 400_000
MAX_REPLY_LENGTH = 400_000


class InferenceRequest(WireModel):
    """Name the model size class and the complete prompt."""

    size: Literal["small", "mid", "big"] = "small"
    prompt: Annotated[str, Field(min_length=1, max_length=MAX_PROMPT_LENGTH)]


class InferenceReply(WireModel):
    """Return the model's text."""

    status: Literal["replied"] = "replied"
    text: Annotated[str, Field(max_length=MAX_REPLY_LENGTH)]


class InferenceUnavailable(WireModel):
    """Report that no model answered, without provider details."""

    status: Literal["unavailable"] = "unavailable"


type InferenceResult = Annotated[InferenceReply | InferenceUnavailable, Field(discriminator="status")]

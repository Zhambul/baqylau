# Copyright (c) 2026 Zhambyl Yermagambet
"""Send worker prompts to the model of the requested size class."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from baqylau_extension_api.models.inference import InferenceReply, InferenceRequest, InferenceUnavailable

from extensions.inference_access import HostInferenceService
from inference.contract import ModelPromptResponse
from inference.errors import ModelUnavailableError

if TYPE_CHECKING:
    from inference.contract import ModelPromptRequest

OWNER = "test.inference"
REQUEST = InferenceRequest(size="mid", prompt="hello")


@dataclass(frozen=True)
class SizedModel:
    """Answer with the size class name, or fail when unavailable."""

    size: str
    available: bool = True

    def send(self, model_prompt_request: ModelPromptRequest) -> ModelPromptResponse:
        """Answer one prompt.

        Returns:
            The size and the prompt.

        Raises:
            ModelUnavailableError: If the fixture model is unavailable.

        """
        if not self.available:
            raise ModelUnavailableError(self.size)
        return ModelPromptResponse(f"{self.size}:{model_prompt_request.prompt}")


@dataclass(frozen=True)
class SizedModels:
    """Return one fixture model per size class."""

    available: bool = True

    def big(self) -> SizedModel:
        """Return the big model.

        Returns:
            The fixture model.

        """
        return SizedModel("big", self.available)

    def mid(self) -> SizedModel:
        """Return the mid model.

        Returns:
            The fixture model.

        """
        return SizedModel("mid", self.available)

    def small(self) -> SizedModel:
        """Return the small model.

        Returns:
            The fixture model.

        """
        return SizedModel("small", self.available)


def test_inference_uses_the_requested_size() -> None:
    """The size class selects the model."""
    assert HostInferenceService(SizedModels(), OWNER).infer(REQUEST) == InferenceReply(text="mid:hello")


def test_failing_model_is_unavailable() -> None:
    """A model that does not answer gives the unavailable state, without provider details."""
    service = HostInferenceService(SizedModels(available=False), OWNER)

    assert service.infer(REQUEST) == InferenceUnavailable()

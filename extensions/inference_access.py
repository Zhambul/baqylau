# Copyright (c) 2026 Zhambyl Yermagambet
"""Send one worker's prompts to the user's configured model of a size class."""

from dataclasses import dataclass

from baqylau_extension_api.contracts.processes import ExtensionInferenceService
from baqylau_extension_api.models import inference

from inference.contract import Model, ModelFactory, ModelPromptRequest
from inference.errors import ModelUnavailableError, ProviderUnavailableError


@dataclass(frozen=True)
class HostInferenceService(ExtensionInferenceService):
    """Send worker prompts to the model of the requested size class."""

    models: ModelFactory
    extension_id: str

    def infer(self, inference_request: inference.InferenceRequest) -> inference.InferenceResult:
        """Send one prompt.

        Returns:
            The model's text, or the unavailable state.

        """
        checked = inference.InferenceRequest.model_validate(inference_request)
        prompt = ModelPromptRequest(checked.prompt, self.extension_id)
        try:
            reply = _model(self.models, checked.size).send(prompt)
        except (ModelUnavailableError, ProviderUnavailableError):
            return inference.InferenceUnavailable()
        return inference.InferenceReply(text=reply.text[:inference.MAX_REPLY_LENGTH])


def _model(models: ModelFactory, size: str) -> Model:
    if size == "big":
        return models.big()
    return models.mid() if size == "mid" else models.small()

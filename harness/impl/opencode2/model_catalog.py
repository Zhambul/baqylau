# Copyright (c) 2026 Zhambyl Yermagambet
"""Read Go models and their native effort values."""

from pydantic import BaseModel, ConfigDict

from harness.impl.opencode2 import native_probe
from harness.impl.opencode2.plugin_info import DEFAULT_MODEL_ID
from harness.models.catalog import EffortOption, ModelOption
from harness.runtime import HarnessRuntimeConfig

_FOREIGN = ConfigDict(extra="ignore", frozen=True)


class NativeVariant(BaseModel):
    """Read one native effort identity."""

    model_config = _FOREIGN
    id: str


class NativeModel(BaseModel):
    """Read model fields supplied by the native catalog."""

    model_config = _FOREIGN
    id: str
    name: str
    variants: tuple[NativeVariant, ...]

    def option(self, default_model: str) -> ModelOption:
        """Build a choice with a supported default effort.

        Returns:
            The dashboard model choice.

        """
        efforts = tuple(variant.id for variant in self.variants)
        default_effort = "low" if "low" in efforts else next(iter(efforts), None)
        return ModelOption(
            f"opencode-go/{self.id}", self.name, self.id == default_model,
            tuple(EffortOption(effort, effort, effort == default_effort) for effort in efforts),
        )


class NativeModels(BaseModel):
    """Read the model list from the plugin method."""

    model_config = _FOREIGN
    models: tuple[NativeModel, ...]


class NativeModelsResponse(BaseModel):
    """Read the native RPC response."""

    model_config = _FOREIGN
    output: NativeModels


def read(harness_runtime_config: HarnessRuntimeConfig) -> tuple[ModelOption, ...]:
    """Read all enabled Go models without a fixed allowlist.

    Returns:
        The model choices, including models with no effort setting.

    """
    response = native_probe.request(harness_runtime_config, "models")
    models = NativeModelsResponse.model_validate_json(response).output.models
    preferred = DEFAULT_MODEL_ID.partition("/")[2]
    fallback = models[0].id if models else ""
    default = next((model.id for model in models if model.id == preferred), fallback)
    return tuple(model.option(default) for model in models)

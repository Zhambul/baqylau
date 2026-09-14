# Copyright (c) 2026 Zhambyl Yermagambet
"""Use native model names and effort values for terminal selection."""

from harness.models.catalog import ModelOption, QueryContext
from harness.models.controls import ControlContext


def option(control_context: ControlContext, model: str) -> ModelOption | None:
    """Find the requested model in the session's configured catalog.

    Returns:
        The model option, or None when it is unavailable.

    """
    plugin = control_context.session.plugin
    if plugin is None or plugin.catalog is None:
        return None
    snapshot = plugin.catalog.read(QueryContext(
        control_context.session.session_id, control_context.session.working_directory,
    ))
    models = plugin.harness_info.models if snapshot.models is None else snapshot.models
    return next((choice for choice in models if choice.model_name == model), None)


def label(model_option: ModelOption) -> str:
    """Build the label shown by the native model list.

    Returns:
        The normalized native name and provider.

    """
    provider = model_option.model_name.partition("/")[0]
    return normalized(f"{model_option.display_name} {provider}")


def effort(model_option: ModelOption, current: str | None) -> str:
    """Keep a supported effort or select the model's default.

    Returns:
        A supported value, or an empty search for the native default.

    """
    available = tuple(level.effort for level in model_option.efforts)
    if current is not None and current in available:
        return current
    return next((level.effort for level in model_option.efforts if level.default), "")


def normalized(label: str) -> str:
    """Normalize the spacing used in native labels.

    Returns:
        A case-insensitive label.

    """
    return label.strip().casefold().replace("-", " ")

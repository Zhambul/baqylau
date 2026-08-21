"""A harness's menus to the new-session form's models.

Two sources, one reply: the per-directory catalogue the plugin discovers, and
the static vocabulary its HarnessInfo declares. The contract keeps them apart;
this is where the browser wants them together.
"""

from __future__ import annotations

from api.application.models.harnesses.harness_catalog_response import (
    CommandOptionResponse,
    EffortOptionResponse,
    HarnessCatalogResponse,
    ModelOptionResponse,
    RewindModeOptionResponse,
)
from harness.models import HarnessCatalogSnapshot, ModelOption, RewindModeOption


def harness_catalog(
    catalog: HarnessCatalogSnapshot,
    models: tuple[ModelOption, ...],
    rewind_modes: tuple[RewindModeOption, ...],
) -> HarnessCatalogResponse:
    return HarnessCatalogResponse(
        commands=tuple(
            CommandOptionResponse(
                command=command.command,
                description=command.description,
                minimum_prompt_count=command.minimum_prompt_count,
            )
            for command in catalog.commands
        ),
        models=tuple(
            ModelOptionResponse(
                model_id=model.model_id,
                display_name=model.display_name,
                default=model.default,
                efforts=tuple(
                    EffortOptionResponse(
                        value=effort.value,
                        display_name=effort.display_name,
                        default=effort.default,
                    )
                    for effort in model.efforts
                ),
            )
            for model in models
        ),
        rewind_modes=tuple(
            RewindModeOptionResponse(value=mode.value, display_name=mode.display_name)
            for mode in rewind_modes
        ),
    )

# Copyright (c) 2026 Zhambyl Yermagambet
"""A harness's menus to the new-session form's models.

Two sources, one reply: the per-directory catalogue the plugin discovers, and
the static vocabulary its HarnessInfo declares. The contract keeps them apart;
this is where the browser wants them together.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from api.application.models.harnesses.harness_catalog_response import (
    CommandOptionResponse,
    EffortOptionResponse,
    HarnessCatalogResponse,
    ModelOptionResponse,
    RewindModeOptionResponse,
)

if TYPE_CHECKING:
    from harness.models.catalog import (
        HarnessCatalogSnapshot,
        ModelOption,
        RewindModeOption,
    )


def harness_catalog(
    harness_catalog_snapshot: HarnessCatalogSnapshot,
    models: tuple[ModelOption, ...],
    rewind_modes: tuple[RewindModeOption, ...],
) -> HarnessCatalogResponse:
    """Return the harness catalog.

    Returns:
        Harness catalog.

    """
    selected_models = models if harness_catalog_snapshot.models is None else harness_catalog_snapshot.models
    return HarnessCatalogResponse(
        commands=tuple(
            CommandOptionResponse(
                command=command.command,
                description=command.description,
                minimum_prompt_count=command.minimum_prompt_count,
            )
            for command in harness_catalog_snapshot.commands
        ),
        models=tuple(
            ModelOptionResponse(
                model_id=model.model_name,
                display_name=model.display_name,
                default=model.default,
                efforts=tuple(
                    EffortOptionResponse(
                        value=effort.effort,
                        display_name=effort.display_name,
                        default=effort.default,
                    )
                    for effort in model.efforts
                ),
            )
            for model in selected_models
        ),
        rewind_modes=tuple(
            RewindModeOptionResponse(value=mode.mode, display_name=mode.display_name) for mode in rewind_modes
        ),
    )

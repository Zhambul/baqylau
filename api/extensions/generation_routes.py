# Copyright (c) 2026 Zhambyl Yermagambet
"""Start, read, catch up, and switch one extension's projection generations."""

from http import HTTPStatus

from fastapi import APIRouter, Depends, HTTPException

from api.extensions.admission import require_catalog_write
from api.extensions.generation_models import ProjectionGenerationResponse
from app.provider_extension_registry import Registry
from app.provider_projections import Generations
from app.provider_reprocessing import Rebuilds
from extensions.projection_rebuild import RebuildRefusedError
from repository.contract.projection_generations import ProjectionGeneration, ProjectionSwitchError

router = APIRouter()
GENERATION_PATH = "/api/extensions/{extension_id}/projections/generations/{generation}"


@router.post(
    "/api/extensions/{extension_id}/projections/rebuild",
    dependencies=[Depends(require_catalog_write)],
    status_code=HTTPStatus.ACCEPTED,
)
def rebuild_projection(extension_id: str, registry: Registry, rebuilds: Rebuilds) -> ProjectionGenerationResponse:
    """Start one candidate generation; the engine builds it from stored facts.

    Returns:
        The new building generation.

    Raises:
        HTTPException: If the extension has no active projector.

    """
    with registry.read_snapshot() as read:
        try:
            generation = rebuilds.request(extension_id, read.snapshot.packages)
        except RebuildRefusedError as error:
            raise HTTPException(HTTPStatus.NOT_FOUND, str(error)) from error
    return generation_response(generation)


@router.get(GENERATION_PATH)
def projection_generation(extension_id: str, generation: str, generations: Generations) -> ProjectionGenerationResponse:
    """Read one generation of an extension.

    Returns:
        The generation and its comparison with the live generation.

    """
    return generation_response(_owned(extension_id, generation, generations))


@router.post(f"{GENERATION_PATH}/activate", dependencies=[Depends(require_catalog_write)])
def activate_projection_generation(
    extension_id: str, generation: str, generations: Generations,
) -> ProjectionGenerationResponse:
    """Make a ready or retired generation live, and keep the previous one for recovery.

    Returns:
        The generation after the switch.

    Raises:
        HTTPException: If the generation is not ready or is behind the live generation.

    """
    _owned(extension_id, generation, generations)
    try:
        return generation_response(generations.switch(generation))
    except ProjectionSwitchError as error:
        raise HTTPException(HTTPStatus.CONFLICT, str(error)) from error


@router.post(f"{GENERATION_PATH}/resume", dependencies=[Depends(require_catalog_write)])
def resume_projection_generation(
    extension_id: str, generation: str, generations: Generations,
) -> ProjectionGenerationResponse:
    """Let a retired generation catch up before a switch back to it.

    Returns:
        The generation, building again.

    Raises:
        HTTPException: If the generation is not retired.

    """
    _owned(extension_id, generation, generations)
    try:
        return generation_response(generations.resume(generation))
    except ProjectionSwitchError as error:
        raise HTTPException(HTTPStatus.CONFLICT, str(error)) from error


def generation_response(projection_generation: ProjectionGeneration) -> ProjectionGenerationResponse:
    """Map one stored generation onto the typed response.

    Returns:
        The typed generation response.

    """
    return ProjectionGenerationResponse(
        generation=projection_generation.generation,
        owner=projection_generation.owner,
        history_revision=projection_generation.history_revision,
        state=projection_generation.state,
        comparison=projection_generation.comparison,
    )


def _owned(extension_id: str, generation: str, generations: Generations) -> ProjectionGeneration:
    stored = generations.read(generation)
    if stored is None or stored.owner != extension_id:
        raise HTTPException(HTTPStatus.NOT_FOUND, "projection generation not found")
    return stored

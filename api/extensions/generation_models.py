# Copyright (c) 2026 Zhambyl Yermagambet
"""Typed projection generation reads and switch replies."""

from pydantic import BaseModel

from repository.contract.projection_generations import GenerationComparison, GenerationState


class ProjectionGenerationResponse(BaseModel):
    """Describe one projection generation, its state, and its comparison with the live one."""

    generation: str
    owner: str
    history_revision: str
    state: GenerationState
    comparison: GenerationComparison | None = None

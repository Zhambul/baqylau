# Copyright (c) 2026 Zhambyl Yermagambet
"""Supply typed core state and proposed changes to a pure projection transform."""

from typing import Annotated

from pydantic import Field

from baqylau_extension_api.core.aggregate import CoreAggregateState
from baqylau_extension_api.models.projection_changes import ProjectionChange
from baqylau_extension_api.models.projections import MAX_PROJECTION_ROWS, ProjectionBinding, ProjectionSelectionRequest
from baqylau_extension_api.models.records import RecordState
from baqylau_extension_api.models.transforms import TransformResult


class ProjectionTransformRequest(ProjectionSelectionRequest):
    """Capture full prior state and the current ordered write proposals."""

    before_core: CoreAggregateState = CoreAggregateState()
    prior_records: Annotated[tuple[RecordState, ...], Field(max_length=MAX_PROJECTION_ROWS)] = ()
    changes: Annotated[tuple[ProjectionChange, ...], Field(max_length=MAX_PROJECTION_ROWS)] = ()


class ProjectionTransformResult(TransformResult[ProjectionChange]):
    """Return explicit operations bound to the exact captured processing state."""

    binding: ProjectionBinding

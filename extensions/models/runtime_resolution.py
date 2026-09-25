# Copyright (c) 2026 Zhambyl Yermagambet
"""Bind complete migration output to its immutable source selection."""

from typing import Annotated, Self

from baqylau_extension_api.models.base import WireModel
from pydantic import Field, model_validator

from extensions.models import resolution_checks
from extensions.models.lifecycle_selection import RuntimeSelection, SettingsChange
from extensions.models.record_migration import RecordGeneration
from extensions.models.runtime_candidates import MigratingRuntimeSelection

MAX_RESOLUTION_BYTES = 8_388_608


class RuntimeResolution(WireModel):
    """Keep converted raw overrides and converted record generations with the complete prepared runtime."""

    runtime: RuntimeSelection
    settings_changes: Annotated[tuple[SettingsChange, ...], Field(max_length=1000)] = ()
    record_generations: Annotated[tuple[RecordGeneration, ...], Field(max_length=1000)] = ()

    @model_validator(mode="after")
    def require_bounded_resolution(self) -> Self:
        """Bound the complete persisted output, not only each conversion reply.

        Returns:
            Complete output within the host storage limit.

        Raises:
            ValueError: If the output has no conversion or the encoded output exceeds the bound.

        """
        if not self.settings_changes and not self.record_generations:
            message = "extension migration resolution has no converted settings or records"
            raise ValueError(message)
        if len(self.model_dump_json().encode()) > MAX_RESOLUTION_BYTES:
            message = "extension migration resolution exceeds its document limit"
            raise ValueError(message)
        return self

    def validate_candidate(self, candidate: MigratingRuntimeSelection) -> None:
        """Reject changed identities, unaffected packages, or raw override structure."""
        resolution_checks.validate_resolution(candidate, self)

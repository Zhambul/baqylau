# Copyright (c) 2026 Zhambyl Yermagambet
"""Convert captured extension data through one pure public capability."""

from typing import Protocol, runtime_checkable

from baqylau_extension_api.models.migration_results import RecordMigrationResult, SettingsMigrationResult
from baqylau_extension_api.models.migrations import RecordMigrationRequest, SettingsMigrationRequest


@runtime_checkable
class ExtensionMigrations(Protocol):
    """Return candidate values without modifying storage or using live services."""

    def migrate_settings(self, settings_request: SettingsMigrationRequest) -> SettingsMigrationResult:
        """Convert the captured settings through an explicitly declared path."""

    def migrate_records(self, records_request: RecordMigrationRequest) -> RecordMigrationResult:
        """Convert every captured row or return one failure for the whole batch."""

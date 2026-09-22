# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep forged raw and effective output consistent to test the final schema boundary."""

from extensions.models.runtime_resolution import RuntimeResolution
from extensions.models.settings import capture_settings
from tests.extension_api import migration_samples
from tests.extension_host.migration_store_fixture import MigrationStore


def invalid_target(case: MigrationStore) -> RuntimeResolution:
    """Change both captured and raw documents to the same schema-invalid value.

    Returns:
        Structurally complete output which must fail the repository schema check.

    """
    package = case.resolution.runtime.packages[0]
    change = case.resolution.settings_changes[0]
    assert change.settings.installation is not None
    document = change.settings.installation.model_copy(update={"json_text": '{"title":42}'})
    overrides = change.settings.model_copy(update={"installation": document})
    target = package.model_copy(update={"settings": capture_settings(migration_samples.manifest(), overrides)})
    return RuntimeResolution(
        runtime=case.resolution.runtime.model_copy(update={"packages": (target,)}),
        settings_changes=(change.model_copy(update={"settings": overrides}),),
    )

# Copyright (c) 2026 Zhambyl Yermagambet
"""A feed replacement view targets a core entry kind or an entry type of its own package."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest import data
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.manifest.validation import validate_manifest

from tests.extension_api import manifest_samples, samples

OWN_ENTRY = f"{samples.EXTENSION_ID}.card"


def replacing(target: str) -> ExtensionManifest:
    """Make the web sample replace one feed target.

    Returns:
        The changed manifest before validation.

    """
    manifest = manifest_samples.web_manifest()
    view_update = {"slot": "feed", "mode": "replace", "target": target}
    view = manifest.contributions.web[0].model_copy(update=view_update)
    contributions = manifest.contributions.model_copy(update={"web": (view,)})
    return manifest.model_copy(update={"contributions": contributions})


def test_replacement_target_is_a_core_entry_kind() -> None:
    """Accept a core entry kind as a feed target and reject an unknown name."""
    assert validate_manifest(replacing("shell_started")).contributions.web[0].target == "shell_started"
    with pytest.raises(ExtensionContractError, match="core entry kind"):
        validate_manifest(replacing("not_an_entry"))


def test_own_entry_type_is_a_target() -> None:
    """A package draws the rows of its own entry type, but an undeclared type is refused."""
    manifest = replacing(OWN_ENTRY)
    schema = samples.schema_definition()
    entry = data.DocumentDefinition(name=OWN_ENTRY, schema_ref=schema.reference, scopes=("session",))
    contributions = manifest.contributions.model_copy(update={"entry_types": (entry,)})
    owned = manifest.model_copy(update={"contributions": contributions, "schemas": (schema,)})

    assert validate_manifest(owned).contributions.web[0].target == OWN_ENTRY
    with pytest.raises(ExtensionContractError, match="entry type of the package"):
        validate_manifest(manifest)

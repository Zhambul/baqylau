# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep dependency graphs and test settings in external package metadata."""

from pathlib import Path

from baqylau_extension_api.manifest.metadata import PackageDependency
from baqylau_extension_api.manifest.settings import SettingsDefinition
from baqylau_extension_api.models.documents import EncodedDocument

from tests.extension_api import service_samples
from tests.extension_host import package_fixture

BASE = "test.base"
CHILD = "test.child"
LEAF = "test.leaf"
OPTIONAL = "test.optional"
PRIVATE_DEFAULT = "private-setting-must-not-appear"


def write_dependency(directory: Path, owner: str, provider: str, *, required: bool = True) -> Path:
    """Write a backend-free consumer with one declared peer dependency.

    Returns:
        The feature-owned mutable source, before the host captures it.

    """
    source = package_fixture.write_package(directory / "packages", owner, web=True)
    manifest = package_fixture.read_manifest(source)
    package_fixture.save_manifest(source, manifest.model_copy(update={"dependencies": (PackageDependency(
        extension_id=provider, version_range=">=1,<2", required=required,
    ),)}))
    return source


def write_graph(directory: Path) -> None:
    """Create required transitive removal and an independent optional consumer."""
    package_fixture.write_package(directory / "packages", BASE, web=True)
    write_dependency(directory, CHILD, BASE)
    write_dependency(directory, LEAF, CHILD)
    write_dependency(directory, OPTIONAL, BASE, required=False)


def write_settings_package(directory: Path) -> Path:
    """Declare a private default to detect accidental state or operation disclosure.

    Returns:
        A web package whose settings use its own registered schema.

    """
    source = package_fixture.write_package(directory / "packages", web=True)
    manifest = package_fixture.read_manifest(source)
    schema = service_samples.schema(package_fixture.OWNER)
    package_fixture.save_manifest(source, manifest.model_copy(update={
        "schemas": (schema,), "settings": SettingsDefinition(
            defaults=EncodedDocument(schema_ref=schema.reference, json_text=f'"{PRIVATE_DEFAULT}"'),
            scopes=("installation", "workspace"),
        ),
    }))
    return source

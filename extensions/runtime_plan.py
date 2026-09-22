# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate complete ready or migrating selections before starting any worker."""

from dataclasses import dataclass

from baqylau_extension_api.manifest.activation import activation_order
from baqylau_extension_api.schemas import SchemaSet
from pydantic import TypeAdapter

from extensions.artifact_contract import ExtensionArtifacts
from extensions.models.artifacts import PackageArtifact
from extensions.models.runtime_candidates import RuntimeCandidate, RuntimePackageCandidate
from extensions.runtime_preparation_contract import RuntimePreparationError


@dataclass(frozen=True)
class PreparationPackage:
    """Bind exact source settings to checked immutable package bytes."""

    selection: RuntimePackageCandidate
    artifact: PackageArtifact


def preparation_plan(selection: RuntimeCandidate, artifacts: ExtensionArtifacts) -> tuple[PreparationPackage, ...]:
    """Check identity, order, schemas, and migration paths without feature calls.

    Returns:
        Complete checked inputs in the accepted dependency order.

    Raises:
        RuntimePreparationError: If selected identity or package order is invalid.

    """
    TypeAdapter(RuntimeCandidate).validate_python(selection)
    packages = tuple(PreparationPackage(
        package, artifacts.read_artifact(package.extension_info.package_digest),
    ) for package in selection.packages)
    expected = tuple(package.extension_info.extension_id for package in selection.packages)
    if activation_order(tuple(package.artifact.manifest for package in packages)) != expected:
        message = "runtime candidate does not use the checked dependency order"
        raise RuntimePreparationError(message)
    schemas = SchemaSet(tuple(
        schema for package in packages for schema in package.artifact.manifest.schemas
    ))
    for package in packages:
        if package.artifact.package_digest != package.selection.extension_info.package_digest:
            message = "captured artifact does not match the selected digest"
            raise RuntimePreparationError(message)
        package.selection.validate_manifest(package.artifact.manifest, schemas)
    return packages

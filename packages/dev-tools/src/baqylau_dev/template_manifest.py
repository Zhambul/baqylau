# Copyright (c) 2026 Zhambyl Yermagambet
"""Build a new package's manifest with the SDK's own models, so its digests and rules match."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from baqylau_extension_api.manifest.e2e import E2eCase, HarnessLimit, TestSurface
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.manifest.validation import validate_manifest
from baqylau_extension_api.versions import API_VERSION

from baqylau_dev import template_declarations as parts
from baqylau_dev.resources import policy_version

if TYPE_CHECKING:
    from pathlib import Path

    from baqylau_extension_api.manifest.data import CapabilityName

    from baqylau_dev.templates import PackageChoice

TEXT_SCHEMA = '{"type":"string"}'


def package_manifest(directory: Path, choice: PackageChoice) -> ExtensionManifest:
    """Build the new package's manifest with the SDK's models, and check it.

    Returns:
        The checked manifest.

    """
    surfaces: list[TestSurface] = ["worker", "api"]
    if choice.web:
        surfaces.append("web")
    if choice.terminal:
        surfaces.append("kitty")
    case = E2eCase(
        case_id="package", path="tests/e2e/test_package.py", surfaces=tuple(surfaces),
        harness_limit=HarnessLimit(reason="The package reads no harness data."),
    )
    capabilities: tuple[CapabilityName, ...] = ("lifecycle", "queries", "terminal") if choice.terminal else (
        "lifecycle", "queries",
    )
    return validate_manifest(ExtensionManifest(
        extension_id=choice.extension_id, name=choice.extension_id, description="A new Baqylau extension.",
        package_version="0.1.0", api_requires=f"=={API_VERSION}", quality_policy=policy_version(),
        backend=parts.backend(choice.module), capabilities=capabilities,
        schemas=(parts.text_schema(choice.extension_id, TEXT_SCHEMA),),
        contributions=parts.contributions(choice.extension_id, web=choice.web, terminal=choice.terminal),
        assets=parts.assets(directory) if choice.web else (), e2e=(case,),
    ))


def text_digest() -> str:
    """Give the digest of the package's text schema.

    Returns:
        The SHA-256 hex digest.

    """
    return hashlib.sha256(TEXT_SCHEMA.encode("utf-8")).hexdigest()

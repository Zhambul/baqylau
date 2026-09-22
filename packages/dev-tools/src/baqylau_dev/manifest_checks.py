# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate manifest files and Python entries before any feature import."""

import ast
import hashlib
from dataclasses import dataclass
from pathlib import Path

from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.manifest.validation import require_compatible_api, validate_manifest

from baqylau_dev.models import ProjectProfile
from baqylau_dev.profiles import require_local_path
from baqylau_dev.source_inventory import SourceModule


@dataclass(frozen=True)
class BackendDeclaration:
    """Keep the checked manifest, module, and exact factory declaration."""

    manifest: ExtensionManifest
    module: SourceModule
    factory: ast.FunctionDef


def checked_backend(
    root: Path, profile: ProjectProfile, modules: tuple[SourceModule, ...],
) -> BackendDeclaration:
    """Read package data and resolve a single synchronous product factory.

    Returns:
        A backend declaration that is safe to use as a static tool root.

    Raises:
        ValueError: If the Python profile has no backend or has an invalid entry.

    """
    manifest = _read_manifest(root, profile)
    if manifest.backend is None:
        message = "the Python extension profile requires a backend declaration"
        raise ValueError(message)
    selected = tuple(module for module in modules if module.name == manifest.backend.module)
    if len(selected) != 1:
        message = "backend module must resolve to one declared product source file"
        raise ValueError(message)
    declarations = tuple(
        node for node in selected[0].tree.body
        if isinstance(node, ast.FunctionDef) and node.name == manifest.backend.factory
    )
    if len(declarations) != 1:
        message = "backend factory must be one module-level synchronous function"
        raise ValueError(message)
    return BackendDeclaration(manifest=manifest, module=selected[0], factory=declarations[0])


def _read_manifest(root: Path, profile: ProjectProfile) -> ExtensionManifest:
    path = require_local_path(root, "extension.json")
    manifest = validate_manifest(ExtensionManifest.model_validate_json(path.read_bytes()))
    require_compatible_api(manifest)
    if manifest.quality_policy != profile.policy_version:
        message = "manifest quality policy differs from the project profile"
        raise ValueError(message)
    _check_test_files(root, profile, manifest)
    _check_assets(root, manifest)
    return manifest


def _check_test_files(root: Path, profile: ProjectProfile, manifest: ExtensionManifest) -> None:
    tests = tuple(require_local_path(root, name) for name in profile.test_roots)
    for case in manifest.e2e:
        path = require_local_path(root, case.path)
        if not path.is_file() or not any(
            path.is_relative_to(test) for test in tests
        ):
            message = f"E2E entry is outside declared test roots: {case.path}"
            raise ValueError(message)


def _check_assets(root: Path, manifest: ExtensionManifest) -> None:
    for asset in manifest.assets:
        path = require_local_path(root, asset.path)
        if not path.is_file() or _file_digest(path) != asset.digest:
            message = f"asset bytes do not match the manifest digest: {asset.path}"
            raise ValueError(message)


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

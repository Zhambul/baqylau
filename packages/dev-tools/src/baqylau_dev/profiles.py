# Copyright (c) 2026 Zhambyl Yermagambet
"""Load explicit project paths without reading feature code."""

import tomllib
from pathlib import Path

from baqylau_dev.models import ProjectProfile
from baqylau_dev.resources import policy_version

PROFILE_FILE = "baqylau-dev.toml"


def load_profile(root: Path) -> ProjectProfile:
    """Validate project configuration and all source paths before running tools.

    Returns:
        An installed-policy profile with existing scoped paths.

    Raises:
        ValueError: If the declared policy release differs.

    """
    document = tomllib.loads((root / PROFILE_FILE).read_text(encoding="utf-8"))
    profile = ProjectProfile.model_validate(document)
    if profile.policy_version != policy_version():
        message = "project policy version differs from the installed baqylau-dev release"
        raise ValueError(message)
    roots = (
        *profile.source_roots, *profile.test_roots, *profile.type_only_roots, *profile.deadcode_roots,
    )
    for name in roots:
        require_local_path(root, name)
    if profile.profile == "extension":
        _require_separate_tests(root, profile)
        _require_standard_excludes(profile)
    return profile


def require_local_path(root: Path, name: str) -> Path:
    """Keep configured input paths inside the explicit project root.

    Returns:
        An existing resolved local path.

    Raises:
        ValueError: If a path is absent, absolute, ambiguous, or outside the root.

    """
    relative = Path(name)
    resolved = (root / relative).resolve()
    if (
        not name or relative.is_absolute() or ".." in relative.parts
        or not resolved.is_relative_to(root.resolve())
    ):
        message = f"invalid project path: {name}"
        raise ValueError(message)
    if not resolved.exists():
        message = f"project path does not exist: {name}"
        raise ValueError(message)
    return resolved


def _require_separate_tests(root: Path, profile: ProjectProfile) -> None:
    for source in profile.source_roots:
        for tests in profile.test_roots:
            if require_local_path(root, tests).is_relative_to(require_local_path(root, source)):
                message = "test roots must be outside product roots for the dead-code gate"
                raise ValueError(message)


def _require_standard_excludes(profile: ProjectProfile) -> None:
    defaults = ProjectProfile.model_fields["design_excludes"].default
    if profile.design_excludes != defaults:
        message = "extension design exclusions must use the shared profile"
        raise ValueError(message)

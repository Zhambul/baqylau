# Copyright (c) 2026 Zhambyl Yermagambet
"""Build fixed tool argument arrays without shell commands or feature imports."""

from pathlib import Path

from baqylau_dev.configuration import configuration_paths
from baqylau_dev.extension_checks import FACTORY_PROBE
from baqylau_dev.models import Gate, ProjectProfile

DEADCODE_DECORATORS = "@*.get,@*.post,@*.put,@*.patch,@*.delete,@*.websocket,@model_validator,@field_validator"
LINT_GATES: tuple[Gate, ...] = ("types", "deadcode", "wemake", "ruff")


def tool_arguments(root: Path, profile: ProjectProfile, gate: Gate) -> tuple[str, ...]:
    """Supply explicit generated configuration for every rule-based tool.

    Returns:
        The fixed module name and argument array.

    Raises:
        ValueError: If the gate has no standalone tool command.

    """
    paths = configuration_paths(root, profile)
    if gate == "types":
        roots = (*profile.source_roots, *profile.type_only_roots, *profile.test_roots)
        probe = (str(root / FACTORY_PROBE),) if profile.profile == "extension" else ()
        return (
            "mypy", "--config-file", str(paths.mypy), *absolute_roots(root, roots), *probe,
        )
    if gate == "deadcode":
        return _deadcode_arguments(root, profile)
    if gate == "wemake":
        return ("flake8", str(root), "--config", str(paths.flake8))
    if gate == "ruff":
        return _ruff_arguments(root, profile)
    if gate in {"unit", "architecture"}:
        return _test_arguments(root, profile, gate)
    message = f"gate has no tool command: {gate}"
    raise ValueError(message)


def absolute_roots(root: Path, paths: tuple[str, ...]) -> tuple[str, ...]:
    """Keep source paths unambiguous when tool configuration is stored elsewhere.

    Returns:
        Absolute input arguments, never option-like relative names.

    """
    return tuple(str(root / path) for path in paths)


def _deadcode_arguments(root: Path, profile: ProjectProfile) -> tuple[str, ...]:
    arguments = (
        "vulture", *absolute_roots(root, (*profile.source_roots, *profile.deadcode_roots)),
        "--ignore-decorators", DEADCODE_DECORATORS,
    )
    if profile.deadcode_excludes:
        return (*arguments, "--exclude", ",".join(profile.deadcode_excludes))
    return arguments


def _ruff_arguments(root: Path, profile: ProjectProfile) -> tuple[str, ...]:
    paths = configuration_paths(root, profile)
    arguments = ("ruff", "check", "--config", str(paths.ruff), str(root))
    if profile.profile == "extension":
        return (*arguments, "--extend-exclude", FACTORY_PROBE)
    return arguments


def _test_arguments(root: Path, profile: ProjectProfile, gate: Gate) -> tuple[str, ...]:
    if gate == "architecture":
        paths = sorted((root / "tests").glob("test_architecture_*.py"))
        if not paths:
            message = "host architecture test inventory is empty"
            raise ValueError(message)
        return ("pytest", "-q", *(str(path) for path in paths))
    arguments = ("pytest", "-q", *absolute_roots(root, profile.test_roots))
    if profile.profile == "host":
        return (*arguments, "-m", "not kitty", "--ignore=tests/e2e")
    return arguments

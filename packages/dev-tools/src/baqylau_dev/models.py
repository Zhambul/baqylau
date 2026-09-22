# Copyright (c) 2026 Zhambyl Yermagambet
"""Read a small quality profile without accepting local rule overrides."""

from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

type Gate = Literal["parity", "architecture", "types", "deadcode", "wemake", "ruff", "unit", "lint"]


class ProjectProfile(BaseModel):
    """Select source paths, not weaker rules or different tool versions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str
    profile: Literal["host", "extension"] = "extension"
    source_roots: tuple[str, ...]
    type_only_roots: tuple[str, ...] = ()
    test_roots: tuple[str, ...] = ("tests",)
    deadcode_roots: tuple[str, ...] = ()
    deadcode_excludes: tuple[str, ...] = ()
    design_excludes: tuple[str, ...] = (".git", ".venv", "node_modules", "build", "dist", ".baqylau-dev")

    @model_validator(mode="after")
    def require_complete_profile(self) -> Self:
        """Keep the host's explicit legacy exceptions out of external packages.

        Returns:
            The checked profile.

        Raises:
            ValueError: If roots are absent or an extension selects host exceptions.

        """
        if not self.source_roots or not self.test_roots:
            message = "source and test roots must not be empty"
            raise ValueError(message)
        if self.profile == "extension" and (self.type_only_roots or self.deadcode_roots or self.deadcode_excludes):
            message = "host-only roots and exclusions are not supported by the extension profile"
            raise ValueError(message)
        return self


class ToolVersion(BaseModel):
    """Report one required tool and its installed version."""

    name: str
    required: str
    installed: str


class ConfigurationPaths(BaseModel):
    """Name complete generated tool configurations."""

    ruff: Path
    mypy: Path
    flake8: Path

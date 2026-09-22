# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate package versions with the standard Python package rules."""

from typing import Annotated

from packaging.specifiers import SpecifierSet
from packaging.version import Version
from pydantic import AfterValidator

from baqylau_extension_api.models.base import NonemptyText

API_VERSION = "0.1.0a1"


def package_version(version: str) -> str:
    """Check a PEP 440 version without changing its source text.

    Returns:
        The validated version text.

    """
    Version(version)
    return version


PackageVersion = Annotated[NonemptyText, AfterValidator(package_version)]


def version_range(requirement: str) -> str:
    """Check an explicit PEP 440 range with the packaging library.

    Returns:
        The validated range text.

    Raises:
        ValueError: If the range is empty.

    """
    if not str(SpecifierSet(requirement)):
        message = "version range must contain an explicit constraint"
        raise ValueError(message)
    return requirement


VersionRange = Annotated[NonemptyText, AfterValidator(version_range)]

# Copyright (c) 2026 Zhambyl Yermagambet
"""Declare the package-owned E2E cases that the shared runner checks for coverage and runs."""

from typing import Annotated, Literal

from pydantic import Field

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest import rules
from baqylau_extension_api.models.base import ExtensionId, Identifier, NonemptyText, WireModel
from baqylau_extension_api.paths import RelativePath

type TestSurface = Literal["web", "kitty", "api", "worker"]


class HarnessLimit(WireModel):
    """Name the only harnesses that a case tests, or none, and tell why.

    A case that does not test every discovered harness needs this limit; the
    runner refuses a missing, stale, or different limit.
    """

    harnesses: Annotated[tuple[Identifier, ...], Field(max_length=100)] = ()
    reason: Annotated[NonemptyText, Field(max_length=1000, pattern=r"\.$")]


class E2eCase(WireModel):
    """Declare one package-owned test file with the surfaces, harnesses, and peers that it covers."""

    case_id: Identifier
    path: RelativePath
    surfaces: Annotated[tuple[TestSurface, ...], Field(min_length=1, max_length=4)]
    harnesses: Annotated[tuple[Identifier, ...], Field(max_length=100)] = ()
    harness_limit: HarnessLimit | None = None
    peers: Annotated[tuple[ExtensionId, ...], Field(max_length=100)] = ()


def validate_case(case: E2eCase, dependencies: frozenset[str]) -> None:
    """Check one case's lists, its harness limit, and its peers.

    Raises:
        ExtensionContractError: If the limit names other harnesses, or a peer is not a declared dependency.

    """
    rules.require_unique(case.surfaces, "E2E surfaces")
    rules.require_unique(case.harnesses, "E2E harnesses")
    rules.require_unique(case.peers, "E2E peers")
    limit = case.harness_limit
    if limit is not None and sorted(limit.harnesses) != sorted(case.harnesses):
        message = f"E2E case {case.case_id} has a harness limit that names other harnesses than the case"
        raise ExtensionContractError(message)
    if not set(case.peers) <= dependencies:
        message = f"E2E case {case.case_id} names a peer that is not a declared dependency"
        raise ExtensionContractError(message)

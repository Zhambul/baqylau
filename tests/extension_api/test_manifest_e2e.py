# Copyright (c) 2026 Zhambyl Yermagambet
"""A declared E2E case keeps its harness limit and peers consistent with the manifest (P08-T02)."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.e2e import E2eCase, HarnessLimit
from baqylau_extension_api.manifest.metadata import PackageDependency
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.manifest.validation import validate_manifest
from pydantic import ValidationError

from tests.extension_api import manifest_samples

CASE = E2eCase(case_id="web", path="tests/e2e/test_web.py", surfaces=("web",), harnesses=("codex",))
CODEX_ONLY = HarnessLimit(harnesses=("codex",), reason="Codex only.")
PEER = "test.peer"


def declared(*cases: E2eCase, dependencies: tuple[PackageDependency, ...] = ()) -> ExtensionManifest:
    """Build the web sample with these cases and dependencies.

    Returns:
        The manifest, not checked yet.

    """
    return manifest_samples.web_manifest().model_copy(update={"e2e": cases, "dependencies": dependencies})


def test_limit_must_name_the_case_harnesses() -> None:
    """A limit that names other harnesses than the case is refused; a matching one is kept."""
    matching = CASE.model_copy(update={"harness_limit": CODEX_ONLY})
    other = CASE.model_copy(update={"harness_limit": HarnessLimit(reason="No harness.")})

    assert validate_manifest(declared(matching)).e2e == (matching,)
    with pytest.raises(ExtensionContractError, match="harness limit"):
        validate_manifest(declared(other))


def test_limit_reason_is_a_sentence() -> None:
    """A limit reason must end as a sentence."""
    with pytest.raises(ValidationError):
        HarnessLimit(reason="no reason")


def test_peer_must_be_a_dependency() -> None:
    """A case can name only a declared dependency as its peer."""
    peer_case = CASE.model_copy(update={"peers": (PEER,)})
    dependency = PackageDependency(extension_id=PEER, version_range=">=1")

    with pytest.raises(ExtensionContractError, match="peer"):
        validate_manifest(declared(peer_case))
    assert validate_manifest(declared(peer_case, dependencies=(dependency,))).e2e == (peer_case,)

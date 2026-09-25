# Copyright (c) 2026 Zhambyl Yermagambet
"""A package with missing E2E coverage fails the shared runner's check (C27, P08-T02)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from baqylau_extension_api.manifest.contributions import Contributions
from baqylau_extension_api.manifest.e2e import E2eCase, HarnessLimit
from baqylau_extension_api.manifest.metadata import PackageDependency
from baqylau_extension_api.manifest.views import TerminalView
from baqylau_extension_testkit.coverage import coverage_findings

from tests.extension_api import manifest_samples

if TYPE_CHECKING:
    from pathlib import Path

    from baqylau_extension_api.manifest.package import ExtensionManifest

HARNESSES = frozenset(("claude_code", "codex"))
WEB: Final = "web"
LIMIT = HarnessLimit(reason="The feature reads no session.")
WEB_CASE = E2eCase(case_id=WEB, path="tests/e2e/test_web.py", surfaces=(WEB,), harness_limit=LIMIT)


def findings(package: Path, manifest: ExtensionManifest, harnesses: frozenset[str] = HARNESSES) -> tuple[str, ...]:
    """Write every case file, then check coverage.

    Returns:
        The findings.

    """
    for case in manifest.e2e:
        (package / case.path).parent.mkdir(parents=True, exist_ok=True)
        (package / case.path).write_text("", encoding="utf-8")
    return coverage_findings(package, manifest, harnesses)


def with_cases(manifest: ExtensionManifest, *cases: E2eCase) -> ExtensionManifest:
    """Replace the declared cases.

    Returns:
        The changed manifest.

    """
    return manifest.model_copy(update={"e2e": cases})


def test_web_view_needs_a_web_case(tmp_path: Path) -> None:
    """A web view with only an API case fails."""
    api_case = WEB_CASE.model_copy(update={"surfaces": ("api",)})

    found = findings(tmp_path, with_cases(manifest_samples.web_manifest(), api_case))

    assert found == ("the package declares a web view but no case covers the web surface",)


def test_terminal_view_needs_a_kitty_case(tmp_path: Path) -> None:
    """A terminal view that only a worker case covers fails, as a unit test is not a Kitty case."""
    terminal = TerminalView(view_id="test.example.pane", title="Pane", scopes=("installation",), pane="example")
    manifest = manifest_samples.backend_manifest().model_copy(update={
        "contributions": Contributions(terminal=(terminal,)),
        "e2e": (E2eCase(case_id="worker", path="tests/test_unit.py", surfaces=("worker",), harness_limit=LIMIT),),
    })

    assert findings(tmp_path, manifest) == (
        "the package declares a terminal view but no case covers the kitty surface",
    )


EVERY_HARNESS = tuple(sorted(HARNESSES))
FULL_CASE = E2eCase(case_id="full", path="tests/test_full.py", surfaces=(WEB,), harnesses=EVERY_HARNESS)


def test_harness_limits_are_checked(tmp_path: Path) -> None:
    """A partial case needs a limit, and a case that tests every harness must not keep one."""
    partial = E2eCase(case_id="partial", path="tests/test_partial.py", surfaces=(WEB,), harnesses=("codex",))
    stale = FULL_CASE.model_copy(update={"harness_limit": HarnessLimit(harnesses=EVERY_HARNESS, reason="All.")})

    assert findings(tmp_path, with_cases(manifest_samples.web_manifest(), partial, stale)) == (
        "case partial: it does not test claude_code and needs a harness limit with a reason",
        "case full: the harness limit is stale; the case tests every harness",
    )


def test_new_harness_needs_coverage(tmp_path: Path) -> None:
    """A complete case has no finding; a harness that the host newly discovers makes it incomplete."""
    manifest = with_cases(manifest_samples.web_manifest(), FULL_CASE)

    assert findings(tmp_path, manifest) == ()
    assert findings(tmp_path, manifest, HARNESSES | {"new"}) == (
        "case full: it does not test new and needs a harness limit with a reason",
    )


def test_peers_and_files_are_required(tmp_path: Path) -> None:
    """A declared dependency without a peer case fails, and so does a case file that is not there."""
    dependency = PackageDependency(extension_id="test.peer", version_range=">=1")
    manifest = with_cases(manifest_samples.web_manifest(), WEB_CASE)
    manifest = manifest.model_copy(update={"dependencies": (dependency,)})

    found = findings(tmp_path, manifest)
    (tmp_path / WEB_CASE.path).unlink()

    assert found == ("no case names the dependency test.peer as a peer",)
    missing = coverage_findings(tmp_path, manifest, HARNESSES)[0]
    assert missing == "case web: tests/e2e/test_web.py is not a file in the package"

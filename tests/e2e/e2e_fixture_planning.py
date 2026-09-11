# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide e2e fixture planning."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from tests.e2e import (
    e2e_fixture_dependencies as fixture_dependencies,
    e2e_fixture_journeys,
    e2e_fixture_reporting,
    e2e_fixture_usage,
    e2e_fixture_work,
)
from tests.harness_names import CLAUDE_CODE_HARNESS, CODEX_HARNESS

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator

SESSION_SCOPE: Literal["session"] = "session"
CLAUDE_PROFILE_NAME = ".claude.json"


def _report_hook(
    **hook_inputs: object,
) -> Generator[
    None, fixture_dependencies.application.pytest.TestReport, fixture_dependencies.application.pytest.TestReport,
]:
    """Pass the report through the pytest hook wrapper.

    Returns:
        The report with any E2E failure diagnostics.

    Yields:
        Control to pytest while the test report is built.

    Require pytest to supply the test item.

    """
    test_item = hook_inputs.get("item")
    message = "pytest did not supply its required item argument"
    assert isinstance(test_item, fixture_dependencies.application.pytest.Item), message
    return (yield from e2e_fixture_work.failure_report(test_item))  # noqa: B901 -- Pluggy wrappers return the hook result.


@fixture_dependencies.application.pytest.fixture(scope=SESSION_SCOPE)
def claude_workspace_trust(
    workspace: str, isolated_claude_home: fixture_dependencies.application.Path,
) -> Iterator[None]:
    """Process claude workspace trust."""
    trust = fixture_dependencies.contexts.repository_testkit.ClaudeCodeProjectTrust.grant(
        isolated_claude_home / CLAUDE_PROFILE_NAME, workspace,
    )
    try:
        yield
    finally:
        trust.close()


@fixture_dependencies.application.pytest.fixture
def repository_workspace(
    workspace: str,
    isolated_codex_home: fixture_dependencies.application.Path,
    isolated_claude_home: fixture_dependencies.application.Path,
) -> Iterator[fixture_dependencies.contexts.repository_testkit.RepositoryWorkspace]:
    """Create a temporary repository trusted by both harnesses.

    Yields:
        The repository workspace for this test.

    """
    with fixture_dependencies.standard.tempfile.TemporaryDirectory(
        prefix="baqylau-repository-", dir=workspace,
    ) as temporary_directory:
        repository = fixture_dependencies.contexts.repository_testkit.RepositoryWorkspace.create(
            fixture_dependencies.application.Path(temporary_directory),
        )
        repository.trust_for_codex(isolated_codex_home)
        claude_code_trust = repository.trust_for_claude_code(isolated_claude_home / CLAUDE_PROFILE_NAME)
        try:
            yield repository
        finally:
            for trust in reversed(claude_code_trust):
                trust.close()


@fixture_dependencies.application.pytest.fixture(scope=SESSION_SCOPE)
def isolated_harness_runtime_configs(
    isolated_codex_home: fixture_dependencies.application.Path,
    isolated_claude_home: fixture_dependencies.application.Path,
) -> fixture_dependencies.harness.harness_runtime.HarnessRuntimeConfigs:
    """Use installed harness executables with isolated test profiles.

    Returns:
        Runtime settings for the Claude Code and Codex test profiles.

    """
    installed = fixture_dependencies.harness.harness_runtime.default_harness_runtime_configs()
    return fixture_dependencies.harness.harness_runtime.HarnessRuntimeConfigs((
        fixture_dependencies.harness.harness_runtime.HarnessRuntimeEntry(
            CLAUDE_CODE_HARNESS,
            fixture_dependencies.harness.harness_runtime.HarnessRuntimeConfig(
                installed.for_harness(CLAUDE_CODE_HARNESS).executable,
                isolated_claude_home,
                isolated_claude_home / "managed-settings.json",
            ),
        ),
        fixture_dependencies.harness.harness_runtime.HarnessRuntimeEntry(
            CODEX_HARNESS,
            fixture_dependencies.harness.harness_runtime.HarnessRuntimeConfig(
                installed.for_harness(CODEX_HARNESS).executable, isolated_codex_home,
            ),
        ),
    ))


@fixture_dependencies.application.pytest.fixture(scope=SESSION_SCOPE)
def application_process(
    pytestconfig: fixture_dependencies.application.pytest.Config,
    tmp_path_factory: fixture_dependencies.application.pytest.TempPathFactory,
    isolated_harness_runtime_configs: fixture_dependencies.harness.harness_runtime.HarnessRuntimeConfigs,
    claude_workspace_trust: None,
) -> Iterator[fixture_dependencies.drivers.process_testkit.ApplicationProcess]:
    """Start an isolated application for this worker's E2E tests.

    Yields:
        The application process, which is stopped after the tests.

    """
    assert claude_workspace_trust is None
    usage_cache = e2e_fixture_usage.e2e_usage_cache()
    runtime_configs = isolated_harness_runtime_configs
    e2e_fixture_journeys.prewarm_usage_cache(
        usage_cache, fixture_dependencies.harness.harness_runtime.default_harness_runtime_configs(),
    )
    data_directory = e2e_fixture_usage.e2e_data_directory(pytestconfig, tmp_path_factory)
    process = fixture_dependencies.drivers.process_testkit.ApplicationProcess.start(
        e2e_fixture_usage.e2e_application_config(data_directory, runtime_configs, usage_cache),
    )
    try:
        yield process
    finally:
        exit_code = process.stop()
        assert exit_code == 0, f"application process exited with {exit_code}"


@fixture_dependencies.application.pytest.fixture
def repository_session_config_context(
    session_config_context: fixture_dependencies.testkit.session_contexts.SessionConfigContext,
    repository_workspace: fixture_dependencies.contexts.repository_testkit.RepositoryWorkspace,
) -> fixture_dependencies.testkit.session_contexts.RepositorySessionConfigContext:
    """Return session configuration services for a repository workspace.

    Returns:
        Session configuration services for a repository workspace.

    """
    return fixture_dependencies.testkit.session_contexts.RepositorySessionConfigContext(
        session_config_context, repository_workspace,
    )


def stall_report(
    application_process: fixture_dependencies.drivers.process_testkit.ApplicationProcess,
    test_item: fixture_dependencies.application.pytest.Item,
    current_marker: tuple[int, int, int],
    started_at: float,
) -> str:
    """Build a diagnostic report for a test with no recorded progress.

    Returns:
        The elapsed time, test identifier, progress marker, and diagnostics.

    """
    diagnostics = fixture_dependencies.harness.failure_diagnostics.e2e_stall_diagnostics(
        application_process, e2e_fixture_reporting.journey_window_ids(test_item),
    )
    elapsed_seconds = round(fixture_dependencies.application.time.monotonic() - started_at)
    return "\n".join((
        "",
        f"E2E stall report after {elapsed_seconds} seconds for {test_item.nodeid}",
        f"progress_marker={current_marker}",
        diagnostics,
        "",
    ))

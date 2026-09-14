# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide e2e fixture usage."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.e2e import e2e_fixture_dependencies as fixture_dependencies

if TYPE_CHECKING:
    from harness.runtime import HarnessRuntimeConfigs

GIT_EXECUTABLE = "git"
GIT_WORKTREE_OPTION = "-C"


def copy_optional_claude_settings(
    source: fixture_dependencies.application.Path, destination: fixture_dependencies.application.Path,
) -> None:
    """Copy optional local settings and consent into the test profile."""
    for filename in ("settings.local.json", "remote-settings-consent.json"):
        source_file = source / filename
        if source_file.exists():
            fixture_dependencies.standard.shutil.copy2(source_file, destination / filename)


@fixture_dependencies.application.pytest.fixture
def versioned_workspace() -> str:
    """Check that the test source directory belongs to a repository with a commit.

    Returns:
        The absolute test source directory path.

    """
    directory = fixture_dependencies.application.Path(__file__).resolve().parents[2]
    fixture_dependencies.standard.subprocess.run(
        (GIT_EXECUTABLE, GIT_WORKTREE_OPTION, str(directory), "rev-parse", "--verify", "HEAD"),
        check=True,
        stdout=fixture_dependencies.standard.subprocess.DEVNULL,
    )
    return str(directory)


def e2e_usage_cache() -> fixture_dependencies.application.Path:
    """Choose the shared usage cache for this test run.

    Returns:
        The cache path with the distributed run identifier, if present.

    """
    run_identity = fixture_dependencies.standard.os.environ.get("PYTEST_XDIST_TESTRUNUID", "single")
    return (
        fixture_dependencies.application.Path(fixture_dependencies.standard.tempfile.gettempdir())
        / f"baqylau-e2e-usage-{run_identity}.json"
    )


def e2e_data_directory(
    pytestconfig: fixture_dependencies.application.pytest.Config,
    tmp_path_factory: fixture_dependencies.application.pytest.TempPathFactory,
) -> fixture_dependencies.application.Path:
    """Create a separate application data directory for each test worker.

    Returns:
        The configured worker directory or a new temporary directory.

    """
    configured = pytestconfig.getoption("--e2e-data-dir")
    if not configured:
        return tmp_path_factory.mktemp("baqylau-live-data")
    data_directory = fixture_dependencies.application.Path(str(configured)).expanduser().resolve()
    distributed_run_identity = fixture_dependencies.standard.os.environ.get("PYTEST_XDIST_TESTRUNUID")
    worker_identity = fixture_dependencies.standard.os.environ.get("PYTEST_XDIST_WORKER")
    if distributed_run_identity and worker_identity:
        data_directory = data_directory / distributed_run_identity / worker_identity
    data_directory.mkdir(parents=True, exist_ok=True)
    return data_directory


def e2e_application_config(
    data_directory: fixture_dependencies.application.Path,
    runtime_configs: HarnessRuntimeConfigs,
    usage_cache: fixture_dependencies.application.Path,
) -> fixture_dependencies.application.ApplicationConfig:
    """Configure an isolated test application with a shared usage cache.

    Returns:
        The application configuration with external notifications disabled.

    """
    return fixture_dependencies.application.ApplicationConfig(
        data_directory=data_directory,
        port=0,
        terminal="pty",
        notify_telegram=False,
        notify_webpush=False,
        harness_runtime_configs=runtime_configs,
        environment_removals=fixture_dependencies.drivers.process_testkit.HARNESS_PARENT_ENVIRONMENT_VARIABLES,
        base_environment={
            **fixture_dependencies.standard.os.environ,
            # OpenCode2 keeps the effort of a new session in the state home. The
            # application writes it there and the session reads it there, so one
            # state home of the test keeps the person's own file untouched.
            "XDG_STATE_HOME": str(
                data_directory / fixture_dependencies.drivers.process_testkit.HARNESS_STATE_DIRECTORY,
            ),
            "BAQYLAU_USAGE_SHARED_CACHE": str(usage_cache),
            "BAQYLAU_USAGE_SHARED_CACHE_SECONDS": "600",
            "BAQYLAU_USAGE_INITIAL_DELAY_SECONDS": "0",
            "BAQYLAU_USAGE_REFRESH_SECONDS": "65",
        },
    )


@fixture_dependencies.application.pytest.fixture
def wait_policy() -> fixture_dependencies.drivers.WaitPolicy:
    """Build the standard E2E wait policy.

    Returns:
        A new wait policy with its default limits.

    """
    return fixture_dependencies.drivers.WaitPolicy()


@fixture_dependencies.application.pytest.fixture
def session_specs() -> fixture_dependencies.drivers.refs.SessionSpecs:
    """Create named session configuration references.

    Returns:
        An empty reference collection for one test.

    """
    return fixture_dependencies.drivers.refs.References("session configuration")

# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide e2e fixture reporting."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from tests.e2e import e2e_fixture_dependencies as fixture_dependencies
from tests.harness_names import CLAUDE_CODE_HARNESS, CODEX_HARNESS

if TYPE_CHECKING:
    from harness.models.usage import UsageRow

json_document = fixture_dependencies.standard.json

DEFAULT_WORKSPACE = str(fixture_dependencies.application.Path("~/code/personal/baqylau-tests").expanduser())
PRIVATE_FILE_MODE = 384
TEXT_ENCODING = "utf-8"
SESSION_SCOPE: Literal["session"] = "session"
GIT_EXECUTABLE = "git"
GIT_WORKTREE_OPTION = "-C"
INVALID_CLAUDE_CREDENTIALS = "Claude credentials are not an object"


def journey_window_ids(test_item: fixture_dependencies.application.pytest.Item) -> frozenset[str] | None:
    """Find the terminal windows owned by the test journey.

    Returns:
        The window identifiers, or None when the test has no journey driver.

    """
    if not isinstance(test_item, fixture_dependencies.application.pytest.Function):
        return None
    driver = test_item.funcargs.get("journey_driver")
    return driver.window_ids if isinstance(driver, fixture_dependencies.contexts.JourneyDriver) else None


class LiveE2EUsageSource:
    """Read usage from the configured live harness accounts."""

    def __init__(self, runtime_configs: fixture_dependencies.harness.harness_runtime.HarnessRuntimeConfigs) -> None:
        """Set up a usage reader for each harness."""
        self.claude = fixture_dependencies.harness.ClaudeCodeUsage(
            runtime_configs.for_harness(CLAUDE_CODE_HARNESS),
        )
        self.codex = fixture_dependencies.harness.CodexUsage(
            runtime_configs.for_harness(CODEX_HARNESS),
        )

    def read(self) -> tuple[UsageRow, ...]:
        """Read usage for both harnesses.

        Returns:
            The Claude and Codex usage rows.

        """
        return (*self.claude.read(), *self.codex.read())


def copy_claude_credentials(
    source: fixture_dependencies.application.Path, destination: fixture_dependencies.application.Path,
) -> None:
    """Seed an isolated profile from Claude's authoritative credential store.

    Raises:
        TypeError: If the decoded credentials are not a JSON object.

    """
    credential_text: str | None = None
    security = fixture_dependencies.standard.shutil.which("security")
    if security is not None:
        result = fixture_dependencies.standard.subprocess.run(
            (security, "find-generic-password", "-w", "-s", "Claude Code-credentials"),
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            credential_text = result.stdout
    if credential_text is None:
        credential_text = (source / ".credentials.json").read_text(encoding=TEXT_ENCODING)
    credentials = fixture_dependencies.standard.json.loads(credential_text)
    if not isinstance(credentials, dict):
        raise TypeError(INVALID_CLAUDE_CREDENTIALS)
    target = destination / ".credentials.json"
    target.write_text(fixture_dependencies.standard.json.dumps(credentials), encoding=TEXT_ENCODING)
    target.chmod(PRIVATE_FILE_MODE)


def pytest_addoption(parser: fixture_dependencies.application.pytest.Parser) -> None:
    """Process pytest addoption."""
    group = parser.getgroup("baqylau live harness tests")
    group.addoption("--e2e-workspace", default=DEFAULT_WORKSPACE)
    group.addoption("--e2e-data-dir", default=None)
    group.addoption("--e2e-model", default=None)
    group.addoption("--e2e-effort", default=None)


@fixture_dependencies.application.pytest.fixture(scope=SESSION_SCOPE)
def workspace(
    pytestconfig: fixture_dependencies.application.pytest.Config,
    tmp_path_factory: fixture_dependencies.application.pytest.TempPathFactory,
) -> str:
    """Copy the configured workspace into an isolated Git repository.

    Returns:
        The path to the copied workspace after its initial commit.

    Raises:
        UsageError: If the configured source directory does not exist.

    """
    source = (
        fixture_dependencies.application.Path(str(pytestconfig.getoption("--e2e-workspace"))).expanduser().resolve()
    )
    if not source.is_dir():
        message = f"workspace does not exist: {source}"
        raise fixture_dependencies.application.pytest.UsageError(message)
    directory = tmp_path_factory.mktemp("baqylau-e2e-workspace")
    fixture_dependencies.standard.shutil.copytree(
        source,
        directory,
        dirs_exist_ok=True,
        ignore=fixture_dependencies.standard.shutil.ignore_patterns(".git", "baqylau-e2e-*.txt"),
    )
    fixture_dependencies.standard.subprocess.run(
        (GIT_EXECUTABLE, GIT_WORKTREE_OPTION, str(directory), "init", "--initial-branch=main"),
        check=True,
        capture_output=True,
        text=True,
    )
    for name, setting_content in (("user.name", "Baqylau E2E"), ("user.email", "baqylau-e2e@example.invalid")):
        fixture_dependencies.standard.subprocess.run(
            (GIT_EXECUTABLE, GIT_WORKTREE_OPTION, str(directory), "config", name, setting_content), check=True,
        )
    fixture_dependencies.standard.subprocess.run(
        (GIT_EXECUTABLE, GIT_WORKTREE_OPTION, str(directory), "add", "."), check=True,
    )
    fixture_dependencies.standard.subprocess.run(
        (GIT_EXECUTABLE, GIT_WORKTREE_OPTION, str(directory), "commit", "-m", "Create E2E workspace"),
        check=True,
        capture_output=True,
        text=True,
    )
    return fixture_dependencies.standard.os.fspath(directory)


def codex_hook_state_entry(
    source_identity: str, state: object, destination: fixture_dependencies.application.Path,
) -> tuple[str, ...]:
    """Build hook state lines for the isolated profile.

    Returns:
        The trusted hook state, or no lines for an invalid entry.

    """
    separator, suffix = source_identity.partition(":")[1:]
    if not isinstance(state, dict):
        return ()
    trusted_hash = state.get("trusted_hash")
    if not separator or not isinstance(trusted_hash, str):
        return ()
    isolated_hook_path = destination / "hooks.json"
    isolated_identity = f"{isolated_hook_path}:{suffix}"
    return (
        "",
        f"[hooks.state.{json_document.dumps(isolated_identity)}]",
        f"trusted_hash = {json_document.dumps(trusted_hash)}",
    )


def isolated_claude_settings(source: fixture_dependencies.application.Path) -> dict[str, object]:
    """Return host settings without plug-ins or telemetry exporters.

    Returns:
        Host settings without plug-ins or telemetry exporters.

    Raises:
        TypeError: If the settings environment is not a JSON object.

    """
    settings: dict[str, object] = fixture_dependencies.standard.json.loads(
        (source / "settings.json").read_text(encoding=TEXT_ENCODING),
    )
    settings["enabledPlugins"] = {}
    settings["extraKnownMarketplaces"] = {}
    settings.pop("statusLine", None)
    settings_environment = settings.setdefault("env", {})
    if not isinstance(settings_environment, dict):
        message = "Claude settings env is not an object"
        raise TypeError(message)
    settings_environment["CLAUDE_CODE_ENABLE_TELEMETRY"] = "0"
    for name in (
        "CLAUDE_OTEL_PORT",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE",
        "OTEL_EXPORTER_OTLP_PROTOCOL",
        "OTEL_METRICS_EXPORTER",
        "OTEL_METRIC_EXPORT_INTERVAL",
    ):
        settings_environment.pop(name, None)
    return settings

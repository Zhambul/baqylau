"""Fixtures for the live harness feature suite."""

from __future__ import annotations

import os
import json
import shutil
import subprocess
import tempfile
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest

from api.runtime import ApplicationConfig
from harness.impl.claude_code.usage.rows import usage_reader as claude_usage_reader
from harness.impl.codex.usage_rows import usage_reader as codex_usage_reader
from harness.models import AccountUsageSnapshot, UsageRow
from harness.services.usage import SharedUsageCache
from sdk.client import BaqylauClient
from tests.e2e.testkit.policy import WaitPolicy
from tests.e2e.testkit.planning import PlanWorkDriver
from tests.e2e.testkit.questions import QuestionWorkDriver
from tests.e2e.testkit.process import (
    HARNESS_PARENT_ENVIRONMENT_VARIABLES,
    ApplicationProcess,
    assert_clean_diagnostics,
)
from tests.e2e.testkit.repository import ClaudeCodeProjectTrust, RepositoryWorkspace
from tests.e2e.testkit.references import (
    AccountSelections,
    ApplicationRestarts,
    Actors,
    ActorMessages,
    Assignments,
    AttachmentBundles,
    BrowserActions,
    BrowserSessionForms,
    Compactions,
    Controls,
    FeedSnapshots,
    FileOperations,
    GlobalStreamUpdates,
    HarnessCatalogs,
    HarnessLists,
    InsightsSnapshots,
    Plans,
    Questions,
    ReasoningTraces,
    References,
    ResumableLists,
    Searches,
    SessionContinuations,
    SessionJourneys,
    SessionSpecs,
    SessionStreamUpdates,
    Sessions,
    Shells,
    StagedAttachments,
    StreamCheckpoints,
    Skills,
    Tasks,
    Turns,
    WebFetches,
    WorkerControls,
    WorktreeChanges,
    Works,
)
from tests.e2e.testkit.skill_fixtures import SkillFixtures, SkillWorkDriver
from tests.e2e.testkit.work import WorkDriver

DEFAULT_WORKSPACE = os.path.expanduser("~/code/personal/baqylau-tests")
FILE_OPERATION_FIXTURE = "baqylau-e2e-file.txt"
FILE_RENAME_SOURCE = "baqylau-e2e-rename-source.txt"
FILE_RENAME_TARGET = "baqylau-e2e-rename-target.txt"
MISSING_FILE_FIXTURE = "baqylau-e2e-missing-file-963.txt"
REWIND_FILE_FIXTURE = "baqylau-e2e-rewind.txt"


class _EmptyUsageRepository:
    def record(self, account_usage_snapshot: AccountUsageSnapshot) -> None:
        del account_usage_snapshot

    def snapshots(self) -> tuple[AccountUsageSnapshot, ...]:
        return ()


class _LiveE2EUsageSource:
    def read(self) -> tuple[UsageRow, ...]:
        repository = _EmptyUsageRepository()
        return (
            *claude_usage_reader.read(repository),
            *codex_usage_reader.read(repository),
        )


def _prewarm_usage_cache(path: Path) -> None:
    """Run the two account probes before twenty daemons compete for them."""
    rows = SharedUsageCache(path, max_age_seconds=600).read(
        _LiveE2EUsageSource()
    )
    claude = next((row for row in rows if row.harness == "claude_code"), None)
    codex = next((row for row in rows if row.harness == "codex"), None)
    if claude is None or claude.collection_error is not None:
        raise AssertionError(
            "Claude usage preflight failed: "
            + ("no usage row" if claude is None else str(claude.collection_error))
        )
    if not any(window.model_name == "fable" for window in claude.windows):
        raise AssertionError("Claude usage preflight has no Fable window")
    if codex is None:
        raise AssertionError("Codex usage preflight has no usage row")


def _copy_claude_credentials(source: Path, destination: Path) -> None:
    """Seed an isolated profile from Claude's authoritative credential store."""
    credential_text: str | None = None
    security = shutil.which("security")
    if security is not None:
        result = subprocess.run(
            (
                security,
                "find-generic-password",
                "-w",
                "-s",
                "Claude Code-credentials",
            ),
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            credential_text = result.stdout
    if credential_text is None:
        credential_text = (source / ".credentials.json").read_text(encoding="utf-8")
    credentials = json.loads(credential_text)
    if not isinstance(credentials, dict):
        raise AssertionError("Claude credentials are not an object")
    target = destination / ".credentials.json"
    target.write_text(json.dumps(credentials), encoding="utf-8")
    target.chmod(0o600)


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("baqylau live harness tests")
    group.addoption("--e2e-workspace", default=DEFAULT_WORKSPACE)
    group.addoption("--e2e-data-dir", default=None)
    group.addoption("--e2e-model", default=None)
    group.addoption("--e2e-effort", default=None)


@pytest.fixture(scope="session")
def workspace(
    pytestconfig: pytest.Config,
    tmp_path_factory: pytest.TempPathFactory,
) -> str:
    source = Path(str(pytestconfig.getoption("--e2e-workspace"))).expanduser().resolve()
    if not source.is_dir():
        raise pytest.UsageError(f"workspace does not exist: {source}")
    directory = tmp_path_factory.mktemp("baqylau-e2e-workspace")
    shutil.copytree(
        source,
        directory,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(".git", "baqylau-e2e-*.txt"),
    )
    subprocess.run(
        ("git", "-C", str(directory), "init", "--initial-branch=main"),
        check=True,
        capture_output=True,
        text=True,
    )
    for name, value in (
        ("user.name", "Baqylau E2E"),
        ("user.email", "baqylau-e2e@example.invalid"),
    ):
        subprocess.run(
            ("git", "-C", str(directory), "config", name, value),
            check=True,
        )
    subprocess.run(("git", "-C", str(directory), "add", "."), check=True)
    subprocess.run(
        ("git", "-C", str(directory), "commit", "-m", "Create E2E workspace"),
        check=True,
        capture_output=True,
        text=True,
    )
    return str(directory)


@pytest.fixture(scope="session")
def isolated_codex_home(
    tmp_path_factory: pytest.TempPathFactory,
    workspace: str,
) -> Path:
    source = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    destination = tmp_path_factory.mktemp("baqylau-e2e-codex-home")
    shutil.copy2(source / "auth.json", destination / "auth.json")
    shutil.copy2(source / "hooks.json", destination / "hooks.json")
    source_config = tomllib.loads((source / "config.toml").read_text(encoding="utf-8"))
    source_hook_states = source_config.get("hooks", {}).get("state", {})
    hook_state_lines = ["[hooks.state]"]
    for source_identity, state in source_hook_states.items():
        _source_path, separator, suffix = source_identity.partition(":")
        trusted_hash = state.get("trusted_hash")
        if not separator or not isinstance(trusted_hash, str):
            continue
        isolated_identity = f"{destination / 'hooks.json'}:{suffix}"
        hook_state_lines.extend(
            (
                "",
                f"[hooks.state.{json.dumps(isolated_identity)}]",
                f"trusted_hash = {json.dumps(trusted_hash)}",
            )
        )
    if len(hook_state_lines) == 1:
        raise AssertionError("Codex E2E hooks have no trusted source entries")
    (destination / "config.toml").write_text(
        "\n".join(
            (
                'approval_policy = "never"',
                'sandbox_mode = "danger-full-access"',
                'service_tier = "default"',
                "",
                f"[projects.{json.dumps(workspace)}]",
                'trust_level = "trusted"',
                "",
                "[features]",
                "apps = false",
                "browser_use = false",
                "in_app_browser = false",
                "default_mode_request_user_input = true",
                "hooks = true",
                "multi_agent_v2 = true",
                "plugin_sharing = false",
                "plugins = false",
                "remote_plugin = false",
                "",
                *hook_state_lines,
                "",
            )
        ),
        encoding="utf-8",
    )
    return destination


@pytest.fixture(scope="session")
def isolated_claude_home(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Path]:
    """One writable Claude profile per xdist worker, with shared auth only."""
    source = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
    destination = tmp_path_factory.mktemp("baqylau-e2e-claude-home")
    _copy_claude_credentials(source, destination)
    for filename in (
        "settings.local.json",
        "remote-settings-consent.json",
    ):
        source_file = source / filename
        if source_file.exists():
            shutil.copy2(source_file, destination / filename)
    settings = json.loads((source / "settings.json").read_text(encoding="utf-8"))
    settings["enabledPlugins"] = {}
    settings["extraKnownMarketplaces"] = {}
    settings.pop("statusLine", None)
    settings_environment = settings.setdefault("env", {})
    if not isinstance(settings_environment, dict):
        raise AssertionError("Claude settings env is not an object")
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
    (destination / "settings.json").write_text(
        json.dumps(settings, sort_keys=True),
        encoding="utf-8",
    )
    source_profile = source / ".claude.json"
    if not source_profile.exists():
        source_profile = Path.home() / ".claude.json"
    shutil.copy2(source_profile, destination / ".claude.json")

    # Claude 2.1.246 asks for interactive consent when an organization-managed
    # setting can export prompts or responses. The host's managed policy is not
    # part of this isolated test profile. Point Claude at an explicit empty
    # policy so the new consent dialog cannot hold the Stop hooks indefinitely.
    managed_settings = destination / "managed-settings.json"
    managed_settings.write_text("{}", encoding="utf-8")

    previous = os.environ.get("CLAUDE_CONFIG_DIR")
    previous_managed_settings = os.environ.get(
        "CLAUDE_CODE_MANAGED_SETTINGS_PATH"
    )
    os.environ["CLAUDE_CONFIG_DIR"] = str(destination)
    os.environ["CLAUDE_CODE_MANAGED_SETTINGS_PATH"] = str(managed_settings)
    try:
        yield destination
    finally:
        if previous is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = previous
        if previous_managed_settings is None:
            os.environ.pop("CLAUDE_CODE_MANAGED_SETTINGS_PATH", None)
        else:
            os.environ["CLAUDE_CODE_MANAGED_SETTINGS_PATH"] = (
                previous_managed_settings
            )


@pytest.fixture(scope="session")
def claude_workspace_trust(
    workspace: str,
    isolated_claude_home: Path,
) -> Iterator[None]:
    trust = ClaudeCodeProjectTrust.grant(
        isolated_claude_home / ".claude.json",
        workspace,
    )
    try:
        yield
    finally:
        trust.close()


@pytest.fixture
def versioned_workspace() -> str:
    directory = Path(__file__).resolve().parents[2]
    subprocess.run(
        ("git", "-C", str(directory), "rev-parse", "--verify", "HEAD"),
        check=True,
        stdout=subprocess.DEVNULL,
    )
    return str(directory)


@pytest.fixture
def repository_workspace(
    workspace: str,
    isolated_codex_home: Path,
    isolated_claude_home: Path,
) -> Iterator[RepositoryWorkspace]:
    with tempfile.TemporaryDirectory(
        prefix="baqylau-repository-",
        dir=workspace,
    ) as temporary_directory:
        repository = RepositoryWorkspace.create(Path(temporary_directory))
        repository.trust_for_codex(isolated_codex_home)
        claude_code_trust = repository.trust_for_claude_code(
            isolated_claude_home / ".claude.json"
        )
        try:
            yield repository
        finally:
            for trust in reversed(claude_code_trust):
                trust.close()


@pytest.fixture(scope="session")
def application_process(
    pytestconfig: pytest.Config,
    tmp_path_factory: pytest.TempPathFactory,
    isolated_codex_home: Path,
    isolated_claude_home: Path,
    claude_workspace_trust: None,
) -> Iterator[ApplicationProcess]:
    del claude_workspace_trust
    run_identity = os.environ.get("PYTEST_XDIST_TESTRUNUID", "single")
    usage_cache = Path(tempfile.gettempdir()) / (
        f"baqylau-e2e-usage-{run_identity}.json"
    )
    _prewarm_usage_cache(usage_cache)
    configured = pytestconfig.getoption("--e2e-data-dir")
    if configured:
        data_directory = Path(str(configured)).expanduser().resolve()
        distributed_run_identity = os.environ.get("PYTEST_XDIST_TESTRUNUID")
        worker_identity = os.environ.get("PYTEST_XDIST_WORKER")
        if distributed_run_identity and worker_identity:
            data_directory = data_directory / distributed_run_identity / worker_identity
        data_directory.mkdir(parents=True, exist_ok=True)
    else:
        data_directory = tmp_path_factory.mktemp("baqylau-live-data")
    process = ApplicationProcess.start(
        ApplicationConfig(
            data_directory=Path(data_directory),
            port=0,
            terminal="pty",
            notify_telegram=False,
            notify_webpush=False,
            environment_removals=HARNESS_PARENT_ENVIRONMENT_VARIABLES,
            base_environment={
                **os.environ,
                "CODEX_HOME": str(isolated_codex_home),
                "CLAUDE_CONFIG_DIR": str(isolated_claude_home),
                # Usage is global account state. One run-scoped snapshot keeps
                # isolated daemons from launching the same native probes.
                "BAQYLAU_USAGE_SHARED_CACHE": str(usage_cache),
                "BAQYLAU_USAGE_SHARED_CACHE_SECONDS": "600",
                "BAQYLAU_USAGE_INITIAL_DELAY_SECONDS": "0",
                "BAQYLAU_USAGE_REFRESH_SECONDS": "65",
            },
        )
    )
    try:
        yield process
    finally:
        exit_code = process.stop()
        assert exit_code == 0, f"application process exited with {exit_code}"


@pytest.fixture(scope="session")
def client(application_process: ApplicationProcess) -> Iterator[BaqylauClient]:
    running = BaqylauClient(application_process.endpoint.url)
    running.application.wait_until_ready()
    start = running.diagnostics.checkpoint()
    try:
        yield running
        end = running.diagnostics.wait_until_drained()
        assert_clean_diagnostics(
            "the complete E2E run has pipeline findings",
            running.diagnostics.report(start, end),
        )
    finally:
        running.close()


@pytest.fixture
def wait_policy() -> WaitPolicy:
    return WaitPolicy()


@pytest.fixture
def session_specs() -> SessionSpecs:
    return References("session configuration")


@pytest.fixture
def account_selections() -> AccountSelections:
    return References("account selection")


@pytest.fixture
def sessions() -> Sessions:
    return References("session")


@pytest.fixture
def session_continuations() -> SessionContinuations:
    return References("session continuation")


@pytest.fixture
def application_restarts() -> ApplicationRestarts:
    return References("application restart")


@pytest.fixture
def session_journeys() -> SessionJourneys:
    return References("session journey")


@pytest.fixture
def turns() -> Turns:
    return References("turn")


@pytest.fixture
def browser_actions() -> BrowserActions:
    return References("browser action")


@pytest.fixture
def works() -> Works:
    return References("work")


@pytest.fixture
def worker_controls() -> WorkerControls:
    return References("worker control")


@pytest.fixture
def feed_snapshots() -> FeedSnapshots:
    return References("feed snapshot")


@pytest.fixture
def stream_checkpoints() -> StreamCheckpoints:
    return References("stream checkpoint")


@pytest.fixture
def session_stream_updates() -> SessionStreamUpdates:
    return References("session stream update")


@pytest.fixture
def global_stream_updates() -> GlobalStreamUpdates:
    return References("global stream update")


@pytest.fixture
def work_driver(
    client: BaqylauClient,
    workspace: str,
    wait_policy: WaitPolicy,
) -> WorkDriver:
    return WorkDriver(client, workspace, wait_policy)


@pytest.fixture
def plan_work_driver(client: BaqylauClient) -> PlanWorkDriver:
    return PlanWorkDriver(client)


@pytest.fixture
def question_work_driver(work_driver: WorkDriver) -> QuestionWorkDriver:
    return QuestionWorkDriver(work_driver)


@pytest.fixture
def skill_fixtures(workspace: str) -> Iterator[SkillFixtures]:
    fixtures = SkillFixtures(workspace)
    try:
        yield fixtures
    finally:
        fixtures.close()


@pytest.fixture
def skill_work_driver(
    work_driver: WorkDriver,
    skill_fixtures: SkillFixtures,
) -> SkillWorkDriver:
    return SkillWorkDriver(work_driver, skill_fixtures)


@pytest.fixture
def shells() -> Shells:
    return References("shell command")


@pytest.fixture
def actors() -> Actors:
    return References("actor")


@pytest.fixture
def actor_messages() -> ActorMessages:
    return References("actor message")


@pytest.fixture
def assignments() -> Assignments:
    return References("assignment")


@pytest.fixture
def file_operations() -> FileOperations:
    return References("file operation")


@pytest.fixture
def searches() -> Searches:
    return References("web search")


@pytest.fixture
def web_fetches() -> WebFetches:
    return References("web fetch")


@pytest.fixture
def reasoning_traces() -> ReasoningTraces:
    return References("reasoning trace")


@pytest.fixture
def worktree_changes() -> WorktreeChanges:
    return References("worktree change")


@pytest.fixture
def staged_attachments() -> StagedAttachments:
    return References("staged attachment")


@pytest.fixture
def attachment_bundles() -> AttachmentBundles:
    return References("attachment bundle")


@pytest.fixture
def controls() -> Controls:
    return References("control")


@pytest.fixture
def skills() -> Skills:
    return References("skill")


@pytest.fixture
def questions() -> Questions:
    return References("question")


@pytest.fixture
def plans() -> Plans:
    return References("plan")


@pytest.fixture
def tasks() -> Tasks:
    return References("task")


@pytest.fixture
def compactions() -> Compactions:
    return References("compaction")


@pytest.fixture
def harness_lists() -> HarnessLists:
    return References("harness list")


@pytest.fixture
def harness_catalogs() -> HarnessCatalogs:
    return References("harness catalog")


@pytest.fixture
def insights_snapshots() -> InsightsSnapshots:
    return References("insights snapshot")


@pytest.fixture
def resumable_lists() -> ResumableLists:
    return References("resumable list")


@pytest.fixture
def browser_session_forms() -> BrowserSessionForms:
    return References("browser session form")


@pytest.fixture
def file_operation_path(workspace: str) -> Iterator[str]:
    path = os.path.join(workspace, FILE_OPERATION_FIXTURE)
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    try:
        yield path
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


@pytest.fixture
def file_rename_paths(workspace: str) -> Iterator[tuple[str, str]]:
    paths = (
        os.path.join(workspace, FILE_RENAME_SOURCE),
        os.path.join(workspace, FILE_RENAME_TARGET),
    )
    for path in paths:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
    try:
        yield paths
    finally:
        for path in paths:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass


@pytest.fixture
def missing_file_path(workspace: str) -> Iterator[str]:
    path = os.path.join(workspace, MISSING_FILE_FIXTURE)
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    try:
        yield path
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


@pytest.fixture
def rewind_file_path(workspace: str) -> Iterator[str]:
    path = os.path.join(workspace, REWIND_FILE_FIXTURE)
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    with open(path, "w", encoding="utf-8") as fixture:
        fixture.write("rewind-baseline-194\n")
    try:
        yield path
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


@pytest.fixture(autouse=True)
def scenario_signoff(
    application_process: ApplicationProcess,
    client: BaqylauClient,
    sessions: Sessions,
    wait_policy: WaitPolicy,
) -> Iterator[None]:
    start = client.diagnostics.checkpoint()
    try:
        yield
        for session in sessions.values():
            snapshot = client.sessions.snapshot(session)
            if snapshot.data.session.state != "finished" and snapshot.data.live:
                receipt = client.sessions.close(session)
                assert receipt.status_code in (200, 202), (
                    f"cleanup action {receipt.request_id!r} was not accepted: {receipt.outcome}"
                )
            client.sessions.wait_until_finished(session, wait_policy.cleanup)
        end = client.diagnostics.wait_until_drained(wait_policy.pipeline)
        assert_clean_diagnostics(
            "the scenario has pipeline findings",
            client.diagnostics.report(start, end),
        )
    finally:
        # A completed turn can leave an interactive CLI waiting in its PTY
        # after its logical session is already finished and no longer appears
        # live. Restarting the isolated per-worker daemon closes every owned
        # PTY and gives the next scenario a clean process/runtime boundary.
        application_process.restart()
        client.application.wait_until_ready()

# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide e2e fixture journeys."""

from __future__ import annotations

from tests.e2e import e2e_fixture_dependencies as fixture_dependencies, e2e_fixture_reporting

json_document = fixture_dependencies.standard.json

USAGE_CACHE_MAX_AGE_SECONDS = 600
TEXT_ENCODING = "utf-8"
CLAUDE_PROFILE_NAME = ".claude.json"


def failure_diagnostic_text(
    application: fixture_dependencies.drivers.process_testkit.ApplicationProcess,
    test_item: fixture_dependencies.application.pytest.Function,
) -> str:
    """Save the full failure report and prepare its test output.

    Returns:
        The test identifier, saved report path, and diagnostic text.

    """
    diagnostics = fixture_dependencies.harness.failure_diagnostics.e2e_failure_diagnostics(
        application, e2e_fixture_reporting.journey_window_ids(test_item),
    )
    report_path = fixture_dependencies.harness.failure_diagnostics.save_e2e_failure_diagnostics(
        application, test_item.nodeid, diagnostics,
    )
    return f"test={test_item.nodeid}\nfull_report={report_path}\n\n{diagnostics}"


def prewarm_usage_cache(
    path: fixture_dependencies.application.Path,
    runtime_configs: fixture_dependencies.harness.harness_runtime.HarnessRuntimeConfigs,
) -> None:
    """Fill the shared cache; the usage scenarios check each provider's result."""
    fixture_dependencies.harness.SharedUsageCache(path, max_age_seconds=USAGE_CACHE_MAX_AGE_SECONDS).read(
        e2e_fixture_reporting.LiveE2EUsageSource(runtime_configs),
    )


def codex_hook_state_lines(
    source: fixture_dependencies.application.Path, destination: fixture_dependencies.application.Path,
) -> list[str]:
    """Read hook state and map its paths to the isolated profile.

    Returns:
        The TOML lines for the isolated hook state.

    """
    source_config = fixture_dependencies.application.tomllib.loads(
        (source / "config.toml").read_text(encoding=TEXT_ENCODING),
    )
    source_hook_states = source_config.get("hooks", {}).get("state", {})
    hook_state_lines = ["[hooks.state]"]
    for source_identity, state in source_hook_states.items():
        if source_identity.partition(":")[0] != str(source / "hooks.json"):
            continue
        hook_state_lines.extend(e2e_fixture_reporting.codex_hook_state_entry(source_identity, state, destination))
    return hook_state_lines


def isolated_codex_config(workspace: str, hook_state_lines: list[str]) -> str:
    """Build the Codex configuration for an isolated test profile.

    Returns:
        The TOML configuration text.

    """
    return "\n".join((
        'approval_policy = "never"',
        'sandbox_mode = "danger-full-access"',
        'service_tier = "default"',
        "",
        "[tools.update_plan]",
        "enabled = true",
        "",
        f"[projects.{json_document.dumps(workspace)}]",
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
    ))


def copy_isolated_claude_settings(
    source: fixture_dependencies.application.Path, destination: fixture_dependencies.application.Path,
) -> None:
    """Copy host settings into an isolated Claude profile."""
    settings = e2e_fixture_reporting.isolated_claude_settings(source)
    (destination / "settings.json").write_text(
        fixture_dependencies.standard.json.dumps(settings, sort_keys=True), encoding=TEXT_ENCODING,
    )
    default_source = fixture_dependencies.application.Path.home() / ".claude"
    source_profile = (
        fixture_dependencies.application.Path.home() / CLAUDE_PROFILE_NAME
        if source == default_source
        else source / CLAUDE_PROFILE_NAME
    )
    fixture_dependencies.standard.shutil.copy2(source_profile, destination / CLAUDE_PROFILE_NAME)


@fixture_dependencies.application.pytest.fixture
def session_config_context(
    session_specs: fixture_dependencies.drivers.refs.SessionSpecs,
    pytestconfig: fixture_dependencies.application.pytest.Config,
) -> fixture_dependencies.testkit.session_contexts.SessionConfigContext:
    """Return common session configuration services.

    Returns:
        Common session configuration services.

    """
    return fixture_dependencies.testkit.session_contexts.SessionConfigContext(session_specs, pytestconfig)


@fixture_dependencies.application.pytest.fixture
def journey_start_context(
    journey_driver: fixture_dependencies.contexts.JourneyDriver,
    session_specs: fixture_dependencies.drivers.refs.SessionSpecs,
    session_journeys: fixture_dependencies.drivers.refs.SessionJourneys,
    sessions: fixture_dependencies.drivers.refs.Sessions,
    turns: fixture_dependencies.drivers.refs.Turns,
) -> fixture_dependencies.harness.journey_contexts.JourneyStartContext:
    """Return services that start journey sessions.

    Returns:
        Services that start journey sessions.

    """
    return fixture_dependencies.harness.journey_contexts.JourneyStartContext(
        journey_driver, session_specs, session_journeys, sessions, turns,
    )

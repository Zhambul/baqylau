# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide test architecture canonical."""

from __future__ import annotations

from tests import (
    architecture_packages,
    architecture_project_dependencies as project_dependencies,
    architecture_standard_dependencies as standard_dependencies,
)

# Keep dependency modules separate from architecture checks.
# isort: split

from tests import (
    architecture_test_canonical,
    architecture_test_databases,
    architecture_test_json,
    architecture_test_protocols,
    architecture_test_syntax,
    architecture_test_tables,
)

ROOT = project_dependencies.Path(__file__).resolve().parents[1]
BIN_DIRECTORY_NAME = "bin"
TEXT_ENCODING = "utf-8"
PYTHON_FILE_PATTERN = "*.py"
IMPLEMENTATION_DIRECTORY_NAME = "impl"
HARNESS_PACKAGE = "harness"
API_PACKAGE = "api"
APP_PACKAGE = "app"
DASHBOARD_PACKAGE = "dashboard"
ENGINE_PACKAGE = "engine"
TERMINAL_PACKAGE = "terminal"
HARNESS_ROOT = ROOT / HARNESS_PACKAGE
HARNESS_IMPLEMENTATION_ROOT = HARNESS_ROOT / IMPLEMENTATION_DIRECTORY_NAME
CLAUDE_CODE_PACKAGE = "claude_code"
DASHBOARD_CLI_PATH = "dashboard/cli.py"
BYTECODE_CACHE_DIRECTORY = "__pycache__"
OUR_PACKAGES = architecture_packages.owned_packages()
AUDIT_RECORD_FLOOR = frozenset((
    "dashboard/cli_lifecycle.py",
    "core/clipboard.py",
    "notify/channels/retraction.py",
    "notify/channels/telegram_alert.py",
    "notify/channels/webpush_fanout.py",
    "notify/channels/webpush_keys.py",
))


def test_only_daemon_and_audit_cli_build_repos() -> None:
    """One process writes. Two others link the layer, and both are reasoned.

    The hook entries, the pane processes, the keybinding and the OTLP receiver are
    HTTP clients of the daemon; they may not import the contract, let alone an
    implementation, and they no longer CAN — they are files in `client/` that
    import nothing of ours (tests/test_canonical_clients.py).
    `app/raw_events_audit_cli.py` is the exception, because it is the tool you run when
    the daemon is the suspect, and it opens read-only. `audit/record.py` is
    the other, because the daemon's own boot and its request guard record before
    and outside the graph that would inject a repository.

    `api/server.py` and `dashboard/cli.py` used to be named here too, for the pid
    lock they shared. The daemon is a singleton because it binds a port, so
    neither one opens anything now: the CLI asks the port who is answering.
    """
    allowed_builders = {
        "app/provider_audit_storage.py",
        "app/provider_auxiliary_storage.py",
        "app/provider_databases.py",
        "app/provider_fact_storage.py",
        "app/provider_goal_dismissals.py",
        "app/provider_harness_launch.py",
        "app/provider_harness_sessions.py",
        "app/provider_preference_storage.py",
        "app/provider_session_storage.py",
        "app/raw_events_audit_cli.py",
        "audit/record.py",
    }
    assert architecture_test_protocols.repository_builders() == allowed_builders
    audit_cli = (ROOT / APP_PACKAGE / "raw_events_audit_cli.py").read_text(encoding=TEXT_ENCODING)
    assert "read_only(" in audit_cli


def test_terminal_storage_is_reached_through() -> None:
    """A route is not its own service, and a renderer does not open a database.

    Two things that used to be here are gone entirely. The view toggle took with
    it the one route in the tree that was its own service — which files the mirror
    has expanded is the PANE's state now, because a file entry carries its own
    diff. And the live screen/keys passthrough went with it: everything a caller
    used it for is a fact in the read model or a control gesture of its own, so
    api/terminal/ is the pane keybindings and nothing else.
    """
    for name in ("api/terminal/panes.py", "terminal/panes/commands.py", "terminal/panes/reaction.py"):
        source = (ROOT / name).read_text(encoding=TEXT_ENCODING)
        assert "repository.contract.terminal" not in source, f"{name} reaches pane storage"
        assert "repository.impl" not in source, f"{name} names an implementation"
    source = (ROOT / API_PACKAGE / TERMINAL_PACKAGE / "panes.py").read_text(encoding=TEXT_ENCODING)
    assert "repository." not in source, "api/terminal/panes.py is a route, not a service"


def test_graph_is_declared_in_one_place() -> None:
    """One process interprets, and it does not assemble an object to do it.

    The `app/provider_*.py` modules declare the APPLICATION nodes with
    `@singleton`, and every consumer — a route, a background
    thread, a test — asks for the node it uses. `api/dependencies.py` is the
    second and last, for the one node that is not the application's: the HTTP
    policy. It lives there because `app/` is the composition root and may not
    import the layer above it.

    The rule this replaced pinned the strings `build_application(` and
    `build_default_application` to two files, because the graph was ONE frozen
    33-field object handed to whoever needed a field of it. There is no such
    object to build now.
    """
    declarers = set()
    for directory in (BIN_DIRECTORY_NAME, *OUR_PACKAGES):
        for path in sorted((ROOT / directory).rglob(PYTHON_FILE_PATTERN)):
            if BYTECODE_CACHE_DIRECTORY in path.parts:
                continue
            if "@singleton" in architecture_test_syntax.code_only(path):
                declarers.add(path.relative_to(ROOT).as_posix())
    expected = {
        provider_path.relative_to(ROOT).as_posix() for provider_path in (ROOT / APP_PACKAGE).glob("provider_*.py")
    }
    expected.add("api/dependencies.py")
    assert declarers == expected


def test_audit_floor_is_only_for_writers_with_no() -> None:
    """Everything with a constructor takes `AuditRecorder`; the floor is the rest.

    `audit/record.py` is the same five writes over a repository nobody
    injected, and it exists for writers that genuinely cannot be handed one: the
    CLI verb that audits a spawn before the daemon exists, and free functions deep
    enough that passing a recorder would mean growing a parameter on every caller
    between here and there. Everything else — the interpreter, the control
    service, the pane commands, the notifier, every route — takes the node.

    Each entry is a decision, not a leftover. A new importer means either a class
    that should have taken the recorder, or a reason stated here.
    """
    assert architecture_test_protocols.audit_floor_importers() == AUDIT_RECORD_FLOOR


def test_no_route_takes_the_whole_graph() -> None:
    """A route signature is its dependency list, or it is a lie.

    Every handler used to take one `ApplicationGraph` — the entire application —
    to reach one or two fields of it, and two of them read `app.state` by hand.
    Both spellings are gone: a handler names the services it uses, and the ONLY
    module that touches the singleton registry is the kernel that owns it.
    """
    banned = ("ApplicationGraph", "canonical_application", "app.state.instances")
    allowed = {"app/injection.py", "api/app.py", "api/error_responses.py"}
    graph_name_violations = [
        violation
        for path in architecture_test_syntax.owned_python_paths()
        for violation in architecture_test_json.graph_name_violations(path, banned, allowed)
    ]
    assert not graph_name_violations


def test_every_declared_node_resolves() -> None:
    """Verify every declared provider resolves as one application graph."""
    injection = standard_dependencies.importlib.import_module("app.injection")
    instances = injection.registry()
    architecture_test_databases.assert_provider_nodes_resolve(
        instances, architecture_test_databases.provider_nodes(architecture_test_databases.provider_modules()),
    )
    architecture_test_tables.assert_registry_is_isolated(instances)


def test_claude_fg_hook_has_no_legacy_drawing() -> None:
    """Verify claude foreground hook has no legacy drawing or state dependency."""
    implementation_files = (
        HARNESS_IMPLEMENTATION_ROOT / CLAUDE_CODE_PACKAGE / "hooks" / "foreground.py",
        HARNESS_IMPLEMENTATION_ROOT / CLAUDE_CODE_PACKAGE / "shell.py",
    )
    forbidden_roots = {API_PACKAGE, APP_PACKAGE, DASHBOARD_PACKAGE, ENGINE_PACKAGE, TERMINAL_PACKAGE}
    foreground_hook_violations = [
        violation
        for path in implementation_files
        for violation in architecture_test_canonical.foreground_hook_violations(path, forbidden_roots)
    ]
    assert not foreground_hook_violations
    assert not (HARNESS_ROOT / IMPLEMENTATION_DIRECTORY_NAME / CLAUDE_CODE_PACKAGE / "foreground_process.py").exists()

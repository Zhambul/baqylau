# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide test architecture repository."""

from __future__ import annotations

from tests import (
    architecture_packages,
    architecture_project_dependencies as project_dependencies,
    architecture_test_files,
    architecture_test_paths,
    architecture_test_providers,
    architecture_test_routes,
    architecture_test_syntax,
    architecture_test_terminals,
)

ROOT = project_dependencies.Path(__file__).resolve().parents[1]
TEXT_ENCODING = "utf-8"
PYTHON_FILE_PATTERN = "*.py"
DOMAIN_PACKAGE = "domain"
REPOSITORY_PACKAGE = "repository"
CORE_PACKAGE = "core"
AUDIT_PACKAGE = "audit"
IMPLEMENTATION_DIRECTORY_NAME = "impl"
HARNESS_PACKAGE = "harness"
CONTRACT_FILE_NAME = "contract.py"
MODELS_DIRECTORY_NAME = "models"
API_PACKAGE = "api"
ENGINE_PACKAGE = "engine"
TERMINAL_PACKAGE = "terminal"
OUR_PACKAGES = architecture_packages.owned_packages()


def test_harness_impls_never_import_app() -> None:
    """`harness/impl/` sits below the graph that composes it.

    A harness may use the contract, the domain, and core utilities; reaching
    for `app/`, `api/` or `dashboard/` would mean a plugin could only run
    inside the daemon — and the hook entries, which run in the harness's own
    process tree, could not import their own package.
    """
    architecture_test_files.assert_imports(
        HARNESS_PACKAGE,
        {CORE_PACKAGE, AUDIT_PACKAGE, DOMAIN_PACKAGE, HARNESS_PACKAGE, REPOSITORY_PACKAGE},
        allowed_modules=frozenset((
            "terminal.contract",
            "terminal.models",
            "terminal.adapter",
            "terminal.launch",
            "terminal.impl",
        )),
    )


def test_terminal_contract_and_models_import() -> None:
    """The floor of the terminal layer: window ids in, typed responses out.

    Keeping it free of `domain`, `harness`, and the rest is what lets the
    harness contract name it, what keeps sessions out of the terminal
    abstraction, and what makes a second terminal implementable against one
    small file.
    """
    boundary = [
        ROOT / TERMINAL_PACKAGE / CONTRACT_FILE_NAME,
        *sorted((ROOT / TERMINAL_PACKAGE / MODELS_DIRECTORY_NAME).rglob(PYTHON_FILE_PATTERN)),
    ]
    foreign = []
    for path in boundary:
        for _path, imported in architecture_test_syntax.imports_under_path(path):
            if architecture_test_paths.is_foreign_terminal_import(imported):
                foreign.append(f"{path.relative_to(ROOT)} imports {imported}")
    assert not foreign


def test_only_provider_graph_resolves_terminal() -> None:
    """`terminal/impl/` has one door and ONE caller.

    Everything else takes a `TerminalPlugin` (or one of its five fields) by
    injection. It used to have three: a hook process and the pane keybinding
    resolved a terminal directly, because they run INSIDE a window and are the
    only things that can observe which one. They are stdlib-only clients now
    (`client/`) and read the variable that names the window straight out of their
    own environment, so the door has no callers left outside the daemon.
    """
    allowed = {"app/provider_runtime.py"}
    importers = {
        relative
        for package in OUR_PACKAGES
        for relative in architecture_test_providers.terminal_implementation_importers(package)
    }
    assert importers == allowed


def test_no_terminal_is_named_outside_its_own() -> None:
    """Verify concrete terminal names stay in their implementation."""
    implementation = ROOT / TERMINAL_PACKAGE / IMPLEMENTATION_DIRECTORY_NAME / "kitty"
    registry = ROOT / TERMINAL_PACKAGE / IMPLEMENTATION_DIRECTORY_NAME / "__init__.py"
    terminal_name_violations = [
        violation
        for path in architecture_test_paths.terminal_vocabulary_paths()
        if (violation := architecture_test_routes.terminal_name_violation(path, implementation, registry)) is not None
    ]
    terminal_name_violations.extend(architecture_test_paths.terminal_asset_violations())
    assert not terminal_name_violations


def test_engine_imports_only_domain_and_harness() -> None:
    """The engine is the neutral middle: evidence in, facts out.

    It may stand on the floor (`core/`, `audit/`) and name the harness
    CONTRACT, because it drives plugins it is handed. Reaching UP — for `app/`,
    `api/`, `dashboard/`, `terminal/` or a concrete harness — would mean the
    store could only run inside the daemon that composes it.
    """
    architecture_test_files.assert_imports(
        ENGINE_PACKAGE,
        {CORE_PACKAGE, AUDIT_PACKAGE, DOMAIN_PACKAGE, ENGINE_PACKAGE, REPOSITORY_PACKAGE},
        allowed_modules=frozenset((
            "harness.contract", "harness.models", "harness.registry", "extensions.manager_contract",
            "extensions.source_processing_contract",
            "extensions.processing_contract", "extensions.interpretation_contract",
            "extensions.observer_pass", "extensions.projection_pass", "extensions.job_scheduling_contract",
            "extensions.projection_rebuild", "extensions.core_projection_transforms", "extensions.pass_health",
            "extensions.mapper.core_events", "extensions.models.interpretation_steps",
            "extensions.models.interpretations", "extensions.models.observations",
            "extensions.models.processing_input",
        )),
    )


def test_sdk_is_outside_client_of_http_api() -> None:
    """The SDK can know the wire contract, but it cannot know the application graph."""
    architecture_test_files.assert_imports("sdk", {API_PACKAGE, "sdk"})


def test_audit_write_tier_is_floor_and_read_tier() -> None:
    """Everything writes audit; only the daemon reads them back.

    `audit/record.py` is reached from hook processes, pane renderers and
    the daemon alike — a floor, like `core/`. `audit/read.py` opens the
    database read-only to answer the dashboard, so a writer that imports it has
    either grown a reporting job or paid for a tier it never uses.
    """
    readers = set()
    for package in OUR_PACKAGES:
        for path, _imported_modules in architecture_test_terminals.imports_under(package):
            if path.relative_to(ROOT).as_posix().startswith("repository/"):
                continue
            if "AuditReadRepository" in path.read_text(encoding=TEXT_ENCODING):
                readers.add(path.relative_to(ROOT).as_posix())
    assert readers == {
        "app/provider_audit_storage.py",
        "app/services/insight_resources.py",
        "app/session_application_resources.py",
    }

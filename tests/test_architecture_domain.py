# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide test architecture domain."""

from __future__ import annotations

from tests import (
    architecture_packages,
    architecture_project_dependencies as project_dependencies,
    architecture_standard_dependencies as standard_dependencies,
    architecture_test_controls,
    architecture_test_files,
    architecture_test_terminals,
)

ROOT = project_dependencies.Path(__file__).resolve().parents[1]
MINIMUM_SCHEMA_TABLE_COUNT = 25
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
HARNESS_ROOT = ROOT / HARNESS_PACKAGE
OUR_PACKAGES = architecture_packages.owned_packages()
DOMAIN_DEPENDENCIES = ("pydantic",)


def test_domain_imports_only_standard_library() -> None:
    """Verify domain imports only the standard library and its one dependency."""
    architecture_test_files.assert_imports(DOMAIN_PACKAGE, {DOMAIN_PACKAGE})
    outside = sorted(
        (
            f"{path.relative_to(ROOT)} imports {imported}"
            for path, imported in architecture_test_terminals.imports_under(DOMAIN_PACKAGE)
            if imported.split(".", 1)[0] not in OUR_PACKAGES
            and imported.split(".", 1)[0] not in project_dependencies.sys.stdlib_module_names
            and (imported.split(".", 1)[0] not in DOMAIN_DEPENDENCIES)
        ),
    )
    assert not outside
    assert architecture_test_files.has_domain_dependency()


def test_repo_layer_imports_only_model_layers() -> None:
    """The new floor: rows in, model objects out, and nothing above it named.

    It may stand on `domain` (the vocabulary), `audit.records` (the
    operational vocabulary, which imports only `domain.ids`), and `core` (where
    its three file paths live), and it may name the two model packages whose
    types its Protocols speak. Reaching for `engine`, `app`, `api`, `dashboard`
    or `notify` would mean the store could only run inside the daemon that
    composes it.
    """
    architecture_test_files.assert_imports(
        REPOSITORY_PACKAGE,
        {CORE_PACKAGE, AUDIT_PACKAGE, DOMAIN_PACKAGE, REPOSITORY_PACKAGE},
        allowed_modules=frozenset((
            "harness.models", "harness.registry", "terminal.models", "extensions.models",
            "extensions.mapper.core_events",
        )),
    )


def test_only_repo_impl_opens_database() -> None:
    """Verify only repository implementations open a database."""
    assert not architecture_test_files.database_access_violations()


def test_repo_contracts_expose_no_connection() -> None:
    """Verify repository contracts do not expose database connections."""
    assert not architecture_test_terminals.repository_contract_violations()


def test_exactly_two_database_files_are_named() -> None:
    """Seven files became two, and the count is the point.

    `main.db` is everything the application owns and reads back; `audit.db` is
    separate because every short-lived process writes it and because it is what
    you read when `main.db` is the suspect. There is no third: the daemon's pid
    claim lived in `locks.db` until the port it binds became the only answer.
    Nothing else may appear.
    """
    named = set()
    for package in OUR_PACKAGES:
        for path in sorted((ROOT / package).rglob(PYTHON_FILE_PATTERN)):
            if path.relative_to(ROOT).as_posix() == "harness/impl/codex/canonical/title_paths.py":
                continue
            named.update(
                standard_dependencies.re.findall(
                    r'"([A-Za-z0-9_.-]+\.(?:db|sqlite))"',
                    path.read_text(encoding=TEXT_ENCODING),
                ),
            )
    assert named == {"main.db", "audit.db"}


def test_no_key_value_table_exists() -> None:
    """Verify the schema has no key-value table."""
    schema_path = ROOT / REPOSITORY_PACKAGE / IMPLEMENTATION_DIRECTORY_NAME / "sqlite" / "schema.py"
    key_value_violations, table_count = architecture_test_controls.key_value_table_violations(
        schema_path.read_text(encoding=TEXT_ENCODING),
    )
    assert not key_value_violations
    assert table_count > MINIMUM_SCHEMA_TABLE_COUNT


def test_harness_contract_and_models_import_only() -> None:
    """The floor of the harness layer, the twin of the terminal one below it.

    The terminal contract is the one thing it may reach sideways for: a
    harness's control context is handed a terminal, and the alternative is an
    untyped field. It is safe because the terminal contract and its models
    import NOTHING of ours (pinned below), so no cycle can form.
    """
    boundary = [
        HARNESS_ROOT / CONTRACT_FILE_NAME,
        *sorted((HARNESS_ROOT / MODELS_DIRECTORY_NAME).rglob(PYTHON_FILE_PATTERN)),
    ]
    allowed_modules = {"terminal.contract", "terminal.models"}
    bad = [
        violation
        for path in boundary
        for violation in architecture_test_terminals.harness_boundary_violations(path, allowed_modules)
    ]
    assert not bad

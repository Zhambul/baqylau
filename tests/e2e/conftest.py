# Copyright (c) 2026 Zhambyl Yermagambet
"""Load E2E fixture plug-ins."""

pytest_plugins = (
    "tests.e2e.e2e_fixture_reporting",
    "tests.e2e.e2e_fixture_usage",
    "tests.e2e.e2e_fixture_workspace",
    "tests.e2e.e2e_fixture_harness_config",
    "tests.e2e.e2e_fixture_application",
    "tests.e2e.e2e_fixture_sessions",
    "tests.e2e.e2e_fixture_accounts",
    "tests.e2e.e2e_fixture_journeys",
    "tests.e2e.e2e_fixture_turns",
    "tests.e2e.e2e_fixture_work",
    "tests.e2e.e2e_fixture_planning",
    "tests.e2e.e2e_fixture_actors",
    "tests.e2e.e2e_fixture_observations",
    "tests.e2e.e2e_fixture_files",
    "tests.e2e.e2e_fixture_controls",
    "tests.e2e.e2e_fixture_catalogs",
    "tests.e2e.e2e_fixture_findings",
    "tests.e2e.e2e_fixture_signoff",
    "tests.e2e.e2e_fixture_lifecycle",
)

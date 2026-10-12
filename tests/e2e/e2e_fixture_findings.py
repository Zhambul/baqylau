# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide the audit rows that scenarios expect, and the pipeline check that excuses them."""

from __future__ import annotations

from typing import Literal

from tests.e2e import e2e_fixture_dependencies as fixture_dependencies

SESSION_SCOPE: Literal["session"] = "session"


@fixture_dependencies.application.pytest.fixture(scope=SESSION_SCOPE)
def expected_audit_errors() -> fixture_dependencies.drivers.expected_findings.ExpectedAuditErrors:
    """Collect the audit rows that the scenarios of this run cause on purpose.

    Returns:
        An empty collection.

    """
    return fixture_dependencies.drivers.expected_findings.ExpectedAuditErrors()


@fixture_dependencies.application.pytest.fixture(scope=SESSION_SCOPE)
def pipeline_check(
    client: fixture_dependencies.harness.BaqylauClient,
    expected_audit_errors: fixture_dependencies.drivers.expected_findings.ExpectedAuditErrors,
) -> fixture_dependencies.drivers.expected_findings.PipelineCheck:
    """Check the pipeline of the test application with the rows that scenarios expect.

    Returns:
        The pipeline check.

    """
    return fixture_dependencies.drivers.expected_findings.PipelineCheck(client, expected_audit_errors)

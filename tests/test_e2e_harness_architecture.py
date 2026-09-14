# Copyright (c) 2026 Zhambyl Yermagambet
"""Verify E2E features select and cover harnesses correctly."""

from __future__ import annotations

import re

from tests import e2e_feature_scenarios as feature_scenarios

FIXED_SESSION = re.compile(rf"session configuration .+ uses (?:{feature_scenarios.HARNESS_NAME})\b")


def selection_violations(scenario: feature_scenarios.FeatureScenario) -> tuple[str, ...]:
    """Return invalid harness-selection rules for one scenario.

    Returns:
        Invalid harness-selection rules for one scenario.

    """
    location = feature_scenarios.scenario_location(scenario)
    violations: list[str] = []
    title = scenario.title.casefold()
    if any(harness.replace("_", " ") in title for harness in feature_scenarios.HARNESSES):
        violations.append(f"{location} names a harness in the behavior title")
    if FIXED_SESSION.search(scenario.behavior):
        violations.append(f"{location} fixes one harness in session setup")
    if "<harness>" not in scenario.behavior:
        return tuple(violations)
    if scenario.kind != "Scenario Outline":
        violations.append(f"{location} uses a harness value without a Scenario Outline")
    if "  Examples:" not in scenario.body:
        violations.append(f"{location} has no Examples table")
    elif not re.search(r"^\s*\|\s*harness\s*\|", scenario.body, re.MULTILINE):
        violations.append(f"{location} has no harness column")
    return tuple(violations)


def coverage_violations(scenario: feature_scenarios.FeatureScenario) -> tuple[str, ...]:
    """Return invalid harness coverage rules for one scenario.

    Returns:
        Invalid harness coverage rules for one scenario.

    """
    location = feature_scenarios.scenario_location(scenario)
    if len(scenario.harness_limit_lines) != len(scenario.harness_limits):
        return (f"{location} has an invalid harness limit comment",)
    if scenario.harnesses == feature_scenarios.HARNESSES:
        if scenario.harness_limits:
            return (f"{location} has a stale harness limit comment",)
        return ()
    if len(scenario.harness_limits) != 1:
        missing = ", ".join(sorted(feature_scenarios.HARNESSES - scenario.harnesses))
        return (f"{location} does not test {missing} and needs one '# Harness limit:' comment",)
    harness_limit = scenario.harness_limits[0]
    violations: list[str] = []
    if scenario.harnesses != harness_limit.harnesses:
        violations.append(
            f"{location} tests {sorted(scenario.harnesses)!r}, but its comment selects "
            f"{sorted(harness_limit.harnesses)!r}",
        )
    if not harness_limit.reason.rstrip().endswith("."):
        violations.append(f"{location} has an incomplete harness limit reason")
    return tuple(violations)


def test_harness_behavior_is_selected_only() -> None:
    """Verify harness behavior is selected only by examples rows."""
    violations = [
        violation for scenario in feature_scenarios.feature_scenarios() for violation in selection_violations(scenario)
    ]
    assert not violations


def test_each_e2e_scenario_covers_each_harness() -> None:
    """Verify each scenario covers each harness or declares a limit."""
    violations = [
        violation for scenario in feature_scenarios.feature_scenarios() for violation in coverage_violations(scenario)
    ]
    assert not violations

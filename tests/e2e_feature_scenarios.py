# Copyright (c) 2026 Zhambyl Yermagambet
"""Read E2E feature scenarios and their harness limits."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from harness.impl.definitions import definitions

if TYPE_CHECKING:
    from collections.abc import Iterator

ROOT = Path(__file__).parents[1]
FEATURES = ROOT / "tests" / "e2e" / "features"
TEXT_ENCODING = "utf-8"
SCENARIO = re.compile(r"^  (Scenario(?: Outline)?):\s*(.+)$", re.MULTILINE)
HARNESSES = frozenset(str(definition.name) for definition in definitions())
HARNESS_NAME = "|".join(re.escape(harness) for harness in sorted(HARNESSES))
HARNESS_ROW = re.compile(rf"^\s*\|\s*({HARNESS_NAME})\s*\|", re.MULTILINE)
HARNESS_LIMIT = re.compile(
    rf"^\s*# Harness limit: (?:(?P<harness>(?:{HARNESS_NAME})(?:,\s*(?:{HARNESS_NAME}))*) only"
    r"|(?P<none>no harness))\. (?P<reason>\S.*)$",
    re.MULTILINE,
)
HARNESS_LIMIT_LINE = re.compile(r"^\s*# Harness limit:.*$", re.MULTILINE)


@dataclass(frozen=True)
class FeatureScenario:
    """Represent one feature scenario."""

    path: Path
    kind: str
    title: str
    body: str

    @property
    def behavior(self) -> str:
        """The scenario body before its examples table."""
        return self.body.split("  Examples:", 1)[0]

    @property
    def harnesses(self) -> frozenset[str]:
        """Harness names declared in the examples rows."""
        return frozenset(match.group(1) for match in HARNESS_ROW.finditer(self.body))

    @property
    def harness_limits(self) -> tuple[HarnessLimit, ...]:
        """Harness limit comments in the scenario behavior."""
        return HarnessLimit.parse(self.behavior)

    @property
    def harness_limit_lines(self) -> tuple[str, ...]:
        """All harness limit comment lines."""
        return tuple(HARNESS_LIMIT_LINE.findall(self.behavior))


@dataclass(frozen=True)
class HarnessLimit:
    """Represent one declared harness limit."""

    harnesses: frozenset[str]
    reason: str

    @classmethod
    def parse(cls, source: str) -> tuple[HarnessLimit, ...]:
        """Read harness limit comments from source text.

        Returns:
            The declared limits with their harness names and reasons.

        """
        return tuple(
            cls(
                frozenset(re.findall(HARNESS_NAME, match.group("harness") or "")),
                match.group("reason"),
            )
            for match in HARNESS_LIMIT.finditer(source)
        )


def scenarios(path: Path) -> tuple[FeatureScenario, ...]:
    """Read the scenarios in one feature file.

    Returns:
        The scenarios in source order.

    """
    source = path.read_text(encoding=TEXT_ENCODING)
    matches = tuple(SCENARIO.finditer(source))
    return tuple(scenarios_from_matches(path, source, matches))


def scenarios_from_matches(
    path: Path,
    source: str,
    matches: tuple[re.Match[str], ...],
) -> Iterator[FeatureScenario]:
    """Read scenarios from source-file matches.

    Yields:
        Each scenario with its path, matched fields, and source body.

    """
    for index, match in enumerate(matches):
        body = scenario_body(source, matches, index, match)
        yield FeatureScenario(path, match.group(1), match.group(2), body)


def scenario_body(
    source: str,
    matches: tuple[re.Match[str], ...],
    index: int,
    match: re.Match[str],
) -> str:
    """Return source text for one scenario match.

    Returns:
        Source text for one scenario match.

    """
    next_index = index + 1
    end = None if next_index >= len(matches) else matches[next_index].start()
    return source[match.start() : end]


def feature_scenarios() -> Iterator[FeatureScenario]:
    """Read all E2E feature scenarios.

    Yields:
        Each scenario in file-name order and then source order.

    """
    for feature_path in sorted(FEATURES.glob("*.feature")):
        yield from scenarios(feature_path)


def scenario_location(feature_scenario: FeatureScenario) -> str:
    """Return a source location for one feature scenario.

    Returns:
        A source location for one feature scenario.

    """
    relative_path = feature_scenario.path.relative_to(ROOT)
    return f"{relative_path}: {feature_scenario.title}"

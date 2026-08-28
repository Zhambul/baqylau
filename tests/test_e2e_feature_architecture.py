"""Architecture rules for harness-neutral live feature files."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).parents[1]
FEATURES = ROOT / "tests" / "e2e" / "features"
SCENARIO = re.compile(r"^  (Scenario(?: Outline)?):\s*(.+)$", re.MULTILINE)
FIXED_SESSION = re.compile(r'session configuration .+ uses (?:codex|claude_code)\b')
HARNESSES = frozenset({"codex", "claude_code"})
HARNESS_LIMIT = re.compile(
    r"^    # Harness limit: (codex|claude_code) only\. (\S.*)$",
    re.MULTILINE,
)


@dataclass(frozen=True)
class FeatureScenario:
    path: Path
    kind: str
    title: str
    body: str

    @property
    def behavior(self) -> str:
        return self.body.split("  Examples:", 1)[0]

    @property
    def harnesses(self) -> frozenset[str]:
        return frozenset(
            match.group(1)
            for match in re.finditer(
                r"^\s*\|\s*(codex|claude_code)\s*\|",
                self.body,
                re.MULTILINE,
            )
        )

    @property
    def harness_limits(self) -> tuple[tuple[str, str], ...]:
        return tuple(HARNESS_LIMIT.findall(self.behavior))


def _scenarios(path: Path) -> tuple[FeatureScenario, ...]:
    source = path.read_text(encoding="utf-8")
    matches = tuple(SCENARIO.finditer(source))
    return tuple(
        FeatureScenario(
            path,
            match.group(1),
            match.group(2),
            source[match.start():(matches[index + 1].start() if index + 1 < len(matches) else None)],
        )
        for index, match in enumerate(matches)
    )


def test_harness_behavior_is_selected_only_by_examples_rows():
    violations = []
    for path in sorted(FEATURES.glob("*.feature")):
        for scenario in _scenarios(path):
            location = f"{path.relative_to(ROOT)}: {scenario.title}"
            if "codex" in scenario.title.casefold() or "claude code" in scenario.title.casefold():
                violations.append(f"{location} names a harness in the behavior title")
            if FIXED_SESSION.search(scenario.behavior):
                violations.append(f"{location} fixes one harness in session setup")
            if "<harness>" not in scenario.behavior:
                continue
            if scenario.kind != "Scenario Outline":
                violations.append(f"{location} uses a harness value without a Scenario Outline")
            if "  Examples:" not in scenario.body:
                violations.append(f"{location} has no Examples table")
            elif not re.search(r"^\s*\|\s*harness\s*\|", scenario.body, re.MULTILINE):
                violations.append(f"{location} has no harness column")

    assert violations == []


def test_shared_harness_behavior_covers_each_harness():
    violations = []
    for path in sorted(FEATURES.glob("*.feature")):
        for scenario in _scenarios(path):
            if not scenario.harnesses:
                continue
            location = f"{path.relative_to(ROOT)}: {scenario.title}"
            if scenario.harnesses == HARNESSES:
                if scenario.harness_limits:
                    violations.append(f"{location} has a stale harness limit comment")
                continue
            if len(scenario.harness_limits) != 1:
                missing = ", ".join(sorted(HARNESSES - scenario.harnesses))
                violations.append(
                    f"{location} does not test {missing} and needs one harness limit comment"
                )
                continue
            limited_harness, reason = scenario.harness_limits[0]
            if scenario.harnesses != {limited_harness}:
                violations.append(
                    f"{location} tests {sorted(scenario.harnesses)!r}, but its comment selects "
                    f"{limited_harness!r}"
                )
            if not reason.rstrip().endswith("."):
                violations.append(f"{location} has an incomplete harness limit reason")

    assert violations == []


def test_worker_behavior_is_selected_per_work_or_by_examples_rows():
    violations = []
    for path in sorted(FEATURES.glob("*.feature")):
        for scenario in _scenarios(path):
            if "<worker>" not in scenario.behavior:
                continue
            location = f"{path.relative_to(ROOT)}: {scenario.title}"
            if scenario.kind != "Scenario Outline":
                violations.append(f"{location} uses a worker value without a Scenario Outline")
            if not re.search(r"^\s*\|.*\bworker\b.*\|", scenario.body, re.MULTILINE):
                violations.append(f"{location} has no worker examples column")

    assert violations == []


def test_e2e_does_not_use_legacy_codex_subagent_tools():
    violations = []
    for path in sorted((ROOT / "tests" / "e2e").rglob("*")):
        if path.suffix not in {".feature", ".py"}:
            continue
        if "multi_agent_v1__" in path.read_text(encoding="utf-8"):
            violations.append(str(path.relative_to(ROOT)))

    assert violations == []


def test_feature_language_does_not_name_native_harness_tools():
    violations = []
    native_tools = re.compile(
        r"multi_agent_v\d+__|\bAgent tool\b|\bSkill tool\b|\$[a-z][a-z0-9-]+"
    )
    for path in sorted(FEATURES.glob("*.feature")):
        if native_tools.search(path.read_text(encoding="utf-8")):
            violations.append(str(path.relative_to(ROOT)))

    assert violations == []

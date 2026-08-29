"""Architecture rules for harness-neutral E2E tests."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from domain.ids import HarnessName


ROOT = Path(__file__).parents[1]
FEATURES = ROOT / "tests" / "e2e" / "features"
SCENARIO = re.compile(r"^  (Scenario(?: Outline)?):\s*(.+)$", re.MULTILINE)
HARNESSES = frozenset(harness.value for harness in HarnessName)
HARNESS_NAME = "|".join(re.escape(harness) for harness in sorted(HARNESSES))
FIXED_SESSION = re.compile(rf"session configuration .+ uses (?:{HARNESS_NAME})\b")
HARNESS_ROW = re.compile(rf"^\s*\|\s*({HARNESS_NAME})\s*\|", re.MULTILINE)
HARNESS_LIMIT = re.compile(
    rf"^\s*# Harness limit: (?:(?P<harness>{HARNESS_NAME}) only|"
    r"(?P<none>no harness))\. (?P<reason>\S.*)$",
    re.MULTILINE,
)
HARNESS_LIMIT_LINE = re.compile(r"^\s*# Harness limit:.*$", re.MULTILINE)


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
            for match in HARNESS_ROW.finditer(self.body)
        )

    @property
    def harness_limits(self) -> tuple[HarnessLimit, ...]:
        return _harness_limits(self.behavior)

    @property
    def harness_limit_lines(self) -> tuple[str, ...]:
        return tuple(HARNESS_LIMIT_LINE.findall(self.behavior))


@dataclass(frozen=True)
class HarnessLimit:
    harnesses: frozenset[str]
    reason: str


def _harness_limits(source: str) -> tuple[HarnessLimit, ...]:
    return tuple(
        HarnessLimit(
            frozenset({match.group("harness")}) if match.group("harness") else frozenset(),
            match.group("reason"),
        )
        for match in HARNESS_LIMIT.finditer(source)
    )


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


def _direct_test_functions(
    tree: ast.Module,
) -> tuple[ast.AsyncFunctionDef | ast.FunctionDef, ...]:
    tests = []
    containers: list[ast.Module | ast.ClassDef] = [tree]
    while containers:
        container = containers.pop()
        for node in container.body:
            if isinstance(node, ast.ClassDef):
                containers.append(node)
            elif isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                if node.name.startswith("test_"):
                    tests.append(node)
    return tuple(tests)


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


def test_each_e2e_scenario_covers_each_harness_or_declares_a_limit():
    violations = []
    for path in sorted(FEATURES.glob("*.feature")):
        for scenario in _scenarios(path):
            location = f"{path.relative_to(ROOT)}: {scenario.title}"
            if len(scenario.harness_limit_lines) != len(scenario.harness_limits):
                violations.append(f"{location} has an invalid harness limit comment")
                continue
            if scenario.harnesses == HARNESSES:
                if scenario.harness_limits:
                    violations.append(f"{location} has a stale harness limit comment")
                continue
            if len(scenario.harness_limits) != 1:
                missing = ", ".join(sorted(HARNESSES - scenario.harnesses))
                violations.append(
                    f"{location} does not test {missing} and needs one "
                    "'# Harness limit:' comment"
                )
                continue
            limit = scenario.harness_limits[0]
            if scenario.harnesses != limit.harnesses:
                violations.append(
                    f"{location} tests {sorted(scenario.harnesses)!r}, but its comment selects "
                    f"{sorted(limit.harnesses)!r}"
                )
            if not limit.reason.rstrip().endswith("."):
                violations.append(f"{location} has an incomplete harness limit reason")

    assert violations == []


def test_direct_python_e2e_tests_declare_a_harness_limit():
    violations = []
    e2e_root = ROOT / "tests" / "e2e"
    for path in sorted(e2e_root.rglob("test_*.py")):
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        for node in _direct_test_functions(ast.parse(source)):
            first_line = min(
                (item.lineno for item in (*node.decorator_list, node)),
                default=node.lineno,
            )
            comment = lines[first_line - 2] if first_line > 1 else ""
            limits = _harness_limits(comment)
            location = f"{path.relative_to(ROOT)}: {node.name}"
            if len(limits) != 1:
                violations.append(
                    f"{location} needs one '# Harness limit:' comment directly above it"
                )
                continue
            if limits[0].harnesses == HARNESSES:
                violations.append(f"{location} has a stale harness limit comment")
            if not limits[0].reason.rstrip().endswith("."):
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

"""The type gate's own guardrails.

Two gates enforce static typing and they check different halves of it: ruff's
ANN rules say every def carries an annotation, mypy says the annotation is true.
Both are ratcheted the same way — a per-package exemption for code whose
migration has not landed — and the exemptions are the only thing standing
between this repo and "all of it is typed".

That makes the exemption lists the thing to guard. An exemption can rot in two
directions and both are silent:

  * the two lists DRIFT, and a package ruff still checks is one mypy no longer
    does (or the reverse), so unannotated code lands through the gap;
  * an exemption OUTLIVES its migration, and goes on protecting whatever is
    written in that package next.

These tests fail on either. Nothing here checks the code — mypy does that, in
`make typecheck`. This checks the gate.
"""

from __future__ import annotations

import configparser
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import tomllib

import pytest


ROOT = pathlib.Path(__file__).resolve().parent.parent


def _mypy_exempt_packages() -> set[str]:
    """The packages mypy.ini stops requiring annotations from."""
    parser = configparser.ConfigParser()
    parser.read(ROOT / "mypy.ini", encoding="utf-8")
    exempt = set()
    for section in parser.sections():
        if not section.startswith("mypy-"):
            continue
        if parser.get(section, "disallow_untyped_defs", fallback="True") != "False":
            continue
        for pattern in section[len("mypy-"):].split(","):
            package = pattern.strip().removesuffix(".*")
            if package:
                exempt.add(package)
    return exempt


def _ruff_exempt_packages() -> set[str]:
    """The packages ruff.toml stops applying the ANN rules to."""
    config = tomllib.loads((ROOT / "ruff.toml").read_text(encoding="utf-8"))
    per_file = config["lint"]["per-file-ignores"]
    exempt = set()
    for glob, codes in per_file.items():
        if not any(code == "ANN" or code.startswith("ANN") for code in codes):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_]+)/\*\*/\*\.py", glob)
        if match:
            exempt.add(match.group(1))
    return exempt


def test_the_two_type_gates_exempt_exactly_the_same_packages():
    """ruff and mypy must ratchet in lockstep.

    They answer different questions, so a package exempted from one and not the
    other is not "half checked" — it is a hole shaped like whichever question
    the remaining gate does not ask. Migrating a package means deleting it from
    BOTH lists, in the same commit, and this is what says so.
    """
    assert _mypy_exempt_packages() == _ruff_exempt_packages()


# pytest.ini's 30s default is meant for the hermetic e2e tests. This one spawns a
# full `mypy --no-incremental` PER exempt package — about 25s of subprocess on a
# warm machine, and more under `-n auto` contention — so it was passing on a
# margin of under a second and failing as a timeout whenever the tree grew.
@pytest.mark.timeout(180)
def test_no_type_exemption_outlives_its_migration():
    """An exemption that is no longer load-bearing must be deleted, not left.

    This is the rule the whole ratchet rests on: exemptions only ever shrink. A
    package that would pass WITHOUT its mypy.ini block is already migrated, and
    leaving the block behind would silently re-open it for the next thing
    written there — which is exactly the failure the gate exists to prevent.

    Checked by re-running mypy against a config with that one package's block
    DELETED, which is exactly what its migration commit will do. (A
    command-line --disallow-untyped-defs would not do: per-module settings win
    over the flag, so the check would pass vacuously for every package.)
    """
    exempt = sorted(_mypy_exempt_packages())
    still_needed = []
    for package in exempt:
        parser = configparser.ConfigParser()
        parser.read(ROOT / "mypy.ini", encoding="utf-8")
        parser.remove_section(f"mypy-{package}.*")
        with tempfile.NamedTemporaryFile(
            "w", suffix=".ini", dir=ROOT, delete=False, encoding="utf-8"
        ) as handle:
            parser.write(handle)
            config_path = handle.name
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "mypy", "--no-incremental",
                 "--config-file", config_path, package],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
        finally:
            os.unlink(config_path)
        if completed.returncode != 0:
            still_needed.append(package)
    assert exempt == still_needed, (
        "these packages now pass without their mypy.ini exemption — delete the "
        "block (and the matching ruff.toml per-file-ignore)"
    )


def test_a_type_ignore_names_the_error_it_silences():
    """`# type: ignore` with no code silences everything, including the next bug.

    ruff's PGH003 enforces this and is the real gate; this test states the rule
    in the suite as well, because the shape it forbids is the one that would
    otherwise accumulate quietly under a green checkmark.
    """
    blanket = []
    for path in ROOT.rglob("*.py"):
        if "__pycache__" in path.parts or ".claude" in path.parts:
            continue
        if path.resolve() == pathlib.Path(__file__).resolve():
            continue   # this file NAMES the shape it forbids
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"#\s*type:\s*ignore(?!\[)", line):
                blanket.append(f"{path.relative_to(ROOT)}:{number}")
    assert blanket == []

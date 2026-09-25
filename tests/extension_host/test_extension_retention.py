# Copyright (c) 2026 Zhambyl Yermagambet
"""Remove only package copies and environments that no stored state refers to (P03-T01)."""

from __future__ import annotations

from contextlib import closing
from typing import TYPE_CHECKING

from extensions.retention import StartupRetention
from repository.impl.sqlite import databases
from repository.impl.sqlite.extension_retention import SqliteRetainedDigests
from tests.extension_host import artifact_fixture, lifecycle_control_fixture as fixtures, package_fixture

if TYPE_CHECKING:
    from pathlib import Path

OWNER = package_fixture.OWNER


def source_digest(case: fixtures.ControlHost) -> str:
    """Read the digest of the discovered source package.

    Returns:
        The current catalog digest of the fixture owner.

    """
    return case.request("enable", "read-digest").package_digest or ""


def select_and_move_on(case: fixtures.ControlHost, source: Path) -> tuple[str, str]:
    """Enable the first copy, capture a second, then replace it with a third source version.

    Returns:
        The enabled digest and the current digest; the second copy has no reference.

    """
    first = source_digest(case)
    case.control.change_lifecycle(OWNER, case.request("enable", "first"))
    case.host.finish()
    package_fixture.write_file(source, "second.txt", b"second")
    case.rescan()
    package_fixture.write_file(source, "third.txt", b"third")
    case.rescan()
    return first, source_digest(case)


def leftovers(artifacts: Path, environments: Path) -> None:
    """Leave an interrupted capture and an environment of a stopped daemon."""
    (artifacts / ".capture-interrupted").mkdir()
    (environments / "environment-left").mkdir(parents=True)


def retention_at(directory: Path) -> StartupRetention:
    """Use the fixture's artifact root, environment root, and main database.

    Returns:
        The startup retention of the fixture application.

    """
    digests = SqliteRetainedDigests(databases.main_database(str(directory / "main.db")))
    return StartupRetention(artifact_fixture.store(directory).root, directory / "extension-environments", digests)


def names(directory: Path) -> list[str]:
    """List the directory entries by name.

    Returns:
        The sorted names.

    """
    return sorted(path.name for path in directory.iterdir())


def test_collect_keeps_referred_copies(tmp_path: Path) -> None:
    """Selected and current copies stay; an old unselected capture, a staging copy, and an environment go."""
    source = package_fixture.write_package(tmp_path / "packages", web=True)
    retention = retention_at(tmp_path)
    with closing(fixtures.open_control(tmp_path)) as case:
        first, current = select_and_move_on(case, source)
        leftovers(retention.artifacts, retention.environments)

        retention.collect()

        assert names(retention.artifacts) == sorted((first, current))
        assert not names(retention.environments)

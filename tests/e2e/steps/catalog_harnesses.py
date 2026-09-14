# Copyright (c) 2026 Zhambyl Yermagambet
"""Steps that check installed harnesses."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pytest_bdd import parsers, then

if TYPE_CHECKING:
    from tests.e2e.testkit.references import HarnessLists


@then(parsers.parse('harness list "{name}" contains {harness}'))
def harness_list_contains(harness_lists: HarnessLists, name: str, harness: str) -> None:
    """Verify the list contains one named harness."""
    found = [harness_info for harness_info in harness_lists.get(name) if harness_info.name == harness]
    found_count = len(found)
    assert found_count == 1, f"harness list {name!r} has {found_count} {harness!r} rows"


@then(parsers.parse('harness list "{name}" has exactly one default'))
def harness_list_has_one_default(harness_lists: HarnessLists, name: str) -> None:
    """Verify exactly one harness is the default."""
    found = [harness_info.name for harness_info in harness_lists.get(name) if harness_info.default_for_launch]
    assert len(found) == 1, f"harness list {name!r} has default harnesses {found}"


@then(parsers.parse('each harness in list "{name}" is launchable'))
def every_harness_is_launchable(harness_lists: HarnessLists, name: str) -> None:
    """Verify all listed harnesses can launch."""
    found = [harness_info.name for harness_info in harness_lists.get(name) if not harness_info.launchable]
    assert not found, f"harness list {name!r} has harnesses that cannot launch: {found}"


@then(parsers.parse('harness {harness} in list "{name}" supports attachments'))
def harness_supports_attachments(harness_lists: HarnessLists, name: str, harness: str) -> None:
    """Verify the dashboard can offer attachment controls."""
    matches = [harness_info for harness_info in harness_lists.get(name) if harness_info.name == harness]
    assert len(matches) == 1
    assert matches[0].supports_attachments, f"harness {harness!r} does not advertise attachments"


@then(parsers.parse('harness {harness} in list "{name}" advertises control {control_name}'))
def harness_advertises_control(
    harness_lists: HarnessLists,
    name: str,
    harness: str,
    control_name: str,
) -> None:
    """Verify a harness advertises one control."""
    matches = [harness_info for harness_info in harness_lists.get(name) if harness_info.name == harness]
    assert len(matches) == 1
    assert control_name in matches[0].control_names, (
        f"harness {harness!r} advertises controls {matches[0].control_names}"
    )


@then(parsers.parse("harness {harness} in list \"{name}\" advertises exactly controls '{control_names}'"))
def harness_advertises_exact_controls(
    harness_lists: HarnessLists,
    name: str,
    harness: str,
    control_names: str,
) -> None:
    """Verify a harness advertises the specified controls."""
    matches = [harness_info for harness_info in harness_lists.get(name) if harness_info.name == harness]
    assert len(matches) == 1
    expected = tuple(sorted(control_names.split(",")))
    assert matches[0].control_names == expected, (
        f"harness {harness!r} advertises controls {matches[0].control_names}; expected {expected}"
    )

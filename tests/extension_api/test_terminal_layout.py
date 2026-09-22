# Copyright (c) 2026 Zhambyl Yermagambet
"""Check layout bounds and local identity rules for complete terminal results."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.terminal.blocks import ListBlock, ListItem, TextBlock
from baqylau_extension_api.terminal.files import MAX_DIFF_CHARACTERS, DiffBlock
from baqylau_extension_api.terminal.layout import MAX_SECTION_CHILDREN, SectionBlock, TerminalBlock
from baqylau_extension_api.terminal.layout_rules import MAX_LAYOUT_BLOCKS, MAX_LAYOUT_DEPTH, validate_layout
from baqylau_extension_api.terminal.models import TerminalView
from baqylau_extension_api.terminal.validation import validate_terminal_view

from tests.extension_api import terminal_example, terminal_samples

TEXT = terminal_example.line("Text")
LIST_ENTRY = ListItem(item_id="one", label=TEXT)


@pytest.mark.parametrize("blocks", [
    (terminal_example.diff(), terminal_example.diff()),
    (ListBlock(block_id="list", entries=(LIST_ENTRY,) * 2),),
    (ListBlock(block_id="list", entries=(), selected_id="absent"),),
    (ListBlock(block_id="list", entries=(ListItem(
        item_id="one", label=TEXT, action_id="absent",
    ),)),),
])
def test_layout_rejects_invalid_references(blocks: tuple[TerminalBlock, ...]) -> None:
    """Do not leave input dispatch with ambiguous targets."""
    with pytest.raises(ExtensionContractError):
        validate_layout(blocks, frozenset())


def nested_layout(depth: int) -> TerminalBlock:
    """Build a small nested layout without an unbounded recursive fixture.

    Returns:
        A leaf under the selected number of sections.

    """
    block: TerminalBlock = TextBlock(block_id="leaf", content=TEXT)
    for index in range(depth):
        block = SectionBlock(block_id=f"section-{index}", title="Section", children=(block,))
    return block


def test_layout_has_an_exact_depth_bound() -> None:
    """Accept the supported depth and reject one more nested level."""
    validate_layout((nested_layout(MAX_LAYOUT_DEPTH),), frozenset())
    with pytest.raises(ExtensionContractError, match="depth limit"):
        validate_layout((nested_layout(MAX_LAYOUT_DEPTH + 1),), frozenset())


def test_layout_bounds_total_blocks() -> None:
    """Reject too many blocks even when each section is within its own limit."""
    section_count = MAX_LAYOUT_BLOCKS // (MAX_SECTION_CHILDREN + 1) + 1
    sections = tuple(SectionBlock(
        block_id=f"section-{index}", title="Group", children=tuple(
            TextBlock(block_id=f"text-{index}-{row}", content=TEXT)
            for row in range(MAX_SECTION_CHILDREN)
        ),
    ) for index in range(section_count))
    with pytest.raises(ExtensionContractError, match="block limit"):
        validate_layout(sections, frozenset())


def test_layout_bounds_combined_encoded_size() -> None:
    """Apply a complete-response byte limit, not just per-field limits."""
    request = terminal_samples.view_request()
    blocks = tuple(
        DiffBlock(block_id=f"diff-{index}", unified_diff="x" * MAX_DIFF_CHARACTERS)
        for index in range(4)
    )
    response = TerminalView(binding=request.binding, title="Large diffs", blocks=blocks)
    with pytest.raises(ExtensionContractError, match="encoded size"):
        validate_terminal_view(request, response)


@pytest.mark.parametrize("change", [
    {"extension_id": "other.package"},
    {"view_id": "test.sample.other"},
    {"runtime_revision": "stale"},
    {"settings_revision": 1},
])
def test_response_requires_the_exact_view_binding(change: dict[str, object]) -> None:
    """Keep late or cross-view output from replacing the requested view."""
    request = terminal_samples.view_request()
    binding = request.binding.model_copy(update=change)
    response = TerminalView(binding=binding, title="Wrong view", blocks=())
    with pytest.raises(ExtensionContractError, match="requested view revision"):
        validate_terminal_view(request, response)

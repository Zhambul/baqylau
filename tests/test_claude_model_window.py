# Copyright (c) 2026 Zhambyl Yermagambet
"""Give each Claude Code model its context window."""

import pytest

from harness.impl.claude_code import model


@pytest.mark.parametrize(
    ("model_name", "context_window"),
    [
        ("haiku", model.LARGE_CONTEXT_WINDOW),
        ("claude-haiku-5-5", model.LARGE_CONTEXT_WINDOW),
        ("claude-haiku-4-5-20251001", model.STANDARD_CONTEXT_WINDOW),
        ("claude-haiku-4-5[1m]", model.STANDARD_CONTEXT_WINDOW),
    ],
)
def test_haiku_five_has_the_large_window(model_name: str, context_window: int) -> None:
    """Haiku 5.5, which the `haiku` alias runs since Claude Code 2.1.294, has a native 1M window."""
    assert model.window(model_name) == context_window

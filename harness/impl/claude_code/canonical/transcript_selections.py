# Copyright (c) 2026 Zhambyl Yermagambet
"""Read the model and effort that a Claude picker command printed."""

import re

from harness.impl.claude_code.model import ClaudeCodeModel

ANSI_SELECTIVE_GRAPHIC_REPRESENTATION = re.compile(r"\x1b\[[0-9;]*m")
MODEL_SELECTION_OUTPUT = re.compile(
    r"^Set model to (?P<model>.+?)(?:\s+and saved as your default for new sessions)?$",
)
EFFORT_SELECTION_OUTPUT = re.compile(r"^Set effort level to (?P<effort>\S+)")
MODEL_SELECTION_SUBJECT = "model"
EFFORT_SELECTION_SUBJECT = "effort"
MODEL_ALIASES = frozenset((
    ClaudeCodeModel.FABLE.value,
    ClaudeCodeModel.OPUS.value,
    ClaudeCodeModel.SONNET.value,
    ClaudeCodeModel.HAIKU.value,
))


def output_selection(text: str) -> tuple[str, str] | None:
    """Read a model or effort selection from local command output.

    The picker forms of `/model` and `/effort` carry their chosen value only
        here. Other command output stays unrecognized.

    Returns:
        The subject name and its selected value, or None for other output.

    """
    model_match = MODEL_SELECTION_OUTPUT.match(text)
    if model_match is not None:
        alias = _model_alias(model_match.group("model"))
        return None if alias is None else (MODEL_SELECTION_SUBJECT, alias)
    effort_match = EFFORT_SELECTION_OUTPUT.match(text)
    if effort_match is not None:
        return (EFFORT_SELECTION_SUBJECT, effort_match.group("effort"))
    return None


def _model_alias(display: str) -> str | None:
    """Read a model alias from the display text a picker printed.

    The text carries decoration and a version, for example
        an ANSI-bolded `Opus 5 (1M context)`.

    Returns:
        The known alias, or None when the text names no known model.

    """
    cleaned = ANSI_SELECTIVE_GRAPHIC_REPRESENTATION.sub("", display).strip().strip("`").strip()
    words = cleaned.removeprefix("claude-").split(maxsplit=1)
    if not words:
        return None
    alias = words[0].split("-", maxsplit=1)[0].lower()
    return alias if alias in MODEL_ALIASES else None

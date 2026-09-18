# Copyright (c) 2026 Zhambyl Yermagambet
"""Parse slash-command wrappers in Claude transcripts."""

import re

from harness.impl.claude_code.model import ClaudeCodeModel

COMMAND_NAME = re.compile(r"<command-name>\s*(/?[^<\n]+?)\s*</command-name>")
COMMAND_ARGUMENTS = re.compile(r"<command-args>\s*([^<]*?)\s*</command-args>")
COMMAND_STANDARD_OUTPUT = re.compile(
    r"^\s*<local-command-stdout>(?P<text>.*)</local-command-stdout>\s*$",
    re.DOTALL,
)
COMMAND_CAVEAT = re.compile(r"^\s*<local-command-caveat>")
COMMAND_OPEN = re.compile(r"^\s*<command-(?:message|name|args)>")
BARE_COMPACT_COMMAND = re.compile(r"/compact(?:\s+([\s\S]*))?", re.IGNORECASE)
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


def command_wrapper(content: str) -> tuple[str, str]:
    """Read an anchored slash-command wrapper.

    Returns:
        The command name and arguments.

    """
    if not COMMAND_OPEN.match(content):
        return "", ""
    return command_parts(content)


def command_parts(content: str) -> tuple[str, str]:
    """Read slash-command parts from wrapped text.

    Returns:
        The command name and arguments.

    """
    name_match = COMMAND_NAME.search(content)
    if not name_match:
        return "", ""
    name = name_match.group(1).strip()
    if not name:
        return "", ""
    arguments_match = COMMAND_ARGUMENTS.search(content)
    return name, arguments_match.group(1).strip() if arguments_match else ""


def command_text(content: str) -> str:
    """Return the slash command as entered.

    Returns:
        The command text.

    """
    name, arguments = command_parts(content)
    if not name:
        return ""
    return f"{name} {arguments}" if arguments else name


def bare_compact_command(content: str) -> tuple[str, str]:
    """Read a bare compact command and its optional instructions.

    Returns:
        The compact command name and instructions.

    """
    match = BARE_COMPACT_COMMAND.fullmatch(content.strip())
    if match is None:
        return "", ""
    return "/compact", (match.group(1) or "").strip()


def command_caveat(content: str) -> bool:
    """Return whether the text is a command caveat.

    Returns:
        True for a command caveat.

    """
    return COMMAND_CAVEAT.match(content) is not None


def command_standard_output_text(content: str) -> str | None:
    """Read the text inside a local command output wrapper.

    Returns:
        The wrapped output text, or None when the wrapper is absent.

    """
    match = COMMAND_STANDARD_OUTPUT.match(content)
    return None if match is None else match.group("text")


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

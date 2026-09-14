# Copyright (c) 2026 Zhambyl Yermagambet
"""Parse slash-command wrappers in Claude transcripts."""

import re

COMMAND_NAME = re.compile(r"<command-name>\s*(/?[^<\n]+?)\s*</command-name>")
COMMAND_ARGUMENTS = re.compile(r"<command-args>\s*([^<]*?)\s*</command-args>")
COMMAND_STANDARD_OUTPUT = re.compile(r"^\s*<local-command-stdout>")
COMMAND_CAVEAT = re.compile(r"^\s*<local-command-caveat>")
COMMAND_OPEN = re.compile(r"^\s*<command-(?:message|name|args)>")
BARE_COMPACT_COMMAND = re.compile(r"/compact(?:\s+([\s\S]*))?", re.IGNORECASE)


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


def command_standard_output(content: str) -> bool:
    """Return whether the text is command output.

    Returns:
        True for command output.

    """
    return COMMAND_STANDARD_OUTPUT.match(content) is not None

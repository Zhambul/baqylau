"""Claude Code shell-command rewriting needed by foreground observation."""

from __future__ import annotations

import os
import re
import shlex

_STATEMENT_SEPARATOR = re.compile(r"\n|;|&&|\|\|")
_CONTINUED_OPERATOR = re.compile(r"(\|\||&&|\|)[ \t]*\n[ \t]*")
_CONTINUED_LINE = re.compile(r"\\[ \t]*\n[ \t]*")


def _statements(command: str) -> list[str]:
    command = _CONTINUED_LINE.sub(" ", command)
    command = _CONTINUED_OPERATOR.sub(r"\1 ", command)
    return [part for part in _STATEMENT_SEPARATOR.split(command) if part.strip()]


def _working_directory(
    statements: list[str],
    initial_directory: str,
    *,
    expand_home: bool = False,
) -> tuple[str, bool]:
    working_directory = initial_directory
    known = True
    for statement in statements:
        try:
            words = shlex.split(statement, posix=True)
        except ValueError:
            return working_directory, False
        if not words or words[0] != "cd":
            continue
        if len(words) != 2:
            known = False
            continue
        target = words[1]
        if expand_home and (target == "~" or target.startswith("~/")):
            target = os.path.expanduser(target)
        if target == "-" or target.startswith("-") or any(
            character in target for character in "$`*?["
        ):
            known = False
        elif os.path.isabs(target):
            working_directory, known = target, True
        elif known:
            working_directory = os.path.normpath(
                os.path.join(working_directory, target)
            )
    return working_directory, known


def copy_output_to(command: str, output_path: str) -> str:
    """Return a command that copies stdout and stderr to `output_path`."""
    quoted_path = shlex.quote(output_path)
    return (
        "{ "
        + command
        + "\n\n} > >(tee -a "
        + quoted_path
        + ") 2> >(tee -a "
        + quoted_path
        + " >&2)"
    )


_COPIED_OUTPUT_SUFFIX = re.compile(
    r"\n\n\} > >\(tee -a (?P<path>\S+)\) 2> >\(tee -a (?P=path) >&2\)\s*\Z"
)


def original_command(command: str) -> str:
    """Remove exactly the foreground copy wrapper produced by this module."""
    if not command.startswith("{ "):
        return command
    match = _COPIED_OUTPUT_SUFFIX.search(command)
    return command[2:match.start()] if match else command


def statement_directories(
    command: str,
    initial_directory: str,
) -> tuple[tuple[str, str | None], ...]:
    """Return each shell statement with its statically known directory."""
    statements = _statements(command)
    rows = []
    for index, statement in enumerate(statements):
        directory, known = _working_directory(
            statements[:index],
            initial_directory,
            expand_home=True,
        )
        rows.append((statement, directory if known else None))
    return tuple(rows)


def redirected_output(command: str, working_directory: str | None) -> tuple[str, bool] | None:
    """Return the final statement's concrete stdout target and append mode."""
    try:
        words = shlex.split(command, posix=False)
    except ValueError:
        return None
    if any(word.startswith("<<") for word in words):
        return None
    statements = _statements(command)
    if not statements:
        return None
    try:
        words = shlex.split(statements[-1], posix=False)
    except ValueError:
        return None

    target = None
    append = False
    word_index = 0
    while word_index < len(words):
        word = words[word_index]
        if word[:1] in ("'", '"'):
            word_index += 1
            continue
        if ">" in word and not word.startswith("2"):
            match = re.match(r"^(?:&|1)?(>>?)(.*)$", word)
            if match:
                remainder = match.group(2)
                if remainder.startswith("|") or remainder.startswith("("):
                    return None
                if remainder:
                    target, append = remainder, match.group(1) == ">>"
                elif word_index + 1 < len(words):
                    next_word = words[word_index + 1]
                    if ">" in next_word or next_word.startswith("("):
                        return None
                    target, append = next_word, match.group(1) == ">>"
                    word_index += 1
        word_index += 1

    if not target or target.startswith("&") or target.startswith("/dev/"):
        return None
    if len(target) >= 2 and target[0] in ("'", '"') and target[-1] == target[0]:
        target = target[1:-1]
    if not target or any(character in target for character in "$`*?[") \
            or target.startswith("~"):
        return None
    if not os.path.isabs(target):
        base, known = _working_directory(
            statements[:-1],
            working_directory or os.getcwd(),
        )
        if not known:
            return None
        target = os.path.join(base, target)
    return target, append

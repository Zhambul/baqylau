"""Find and copy Claude Code shell output without running the command."""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass

import bashlex  # type: ignore[import-untyped]


@dataclass(frozen=True)
class RedirectedOutput:
    path: str
    append: bool


@dataclass
class ShellDirectory:
    path: str
    known: bool = True

    def copy(self) -> ShellDirectory:
        return ShellDirectory(self.path, self.known)


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


_OUTPUT_REDIRECTS = (">", ">>", ">|", "&>", "&>>")
_APPEND_REDIRECTS = (">>", "&>>")


def _literal_word(node: bashlex.ast.node) -> str | None:
    if getattr(node, "kind", None) != "word":
        return None
    parts = getattr(node, "parts", ())
    if any(getattr(part, "kind", None) != "tilde" for part in parts):
        return None
    word = str(node.word)
    if word == "~" or word.startswith("~/"):
        word = os.path.expanduser(word)
    if not word or any(character in word for character in "$`*?["):
        return None
    return word


def _output_path(
    node: bashlex.ast.node,
    shell_directory: ShellDirectory,
) -> str | None:
    target = _literal_word(node)
    if target is None or target == "-" or target.startswith("/dev/"):
        return None
    if os.path.isabs(target):
        return os.path.normpath(target)
    if not shell_directory.known:
        return None
    return os.path.normpath(os.path.join(shell_directory.path, target))


def _word_nodes(command: bashlex.ast.node) -> list[bashlex.ast.node]:
    return [part for part in getattr(command, "parts", ()) if part.kind == "word"]


def _redirects(
    command: bashlex.ast.node,
    shell_directory: ShellDirectory,
) -> list[RedirectedOutput]:
    found: list[RedirectedOutput] = []
    for part in getattr(command, "parts", ()):
        redirect_type = getattr(part, "type", None)
        if getattr(part, "kind", None) != "redirect" or redirect_type not in _OUTPUT_REDIRECTS:
            continue
        path = _output_path(getattr(part, "output", None), shell_directory)
        if path is not None:
            found.append(RedirectedOutput(path, redirect_type in _APPEND_REDIRECTS))
    return found


def _tee_outputs(
    command: bashlex.ast.node,
    shell_directory: ShellDirectory,
) -> list[RedirectedOutput]:
    words = _word_nodes(command)
    if not words:
        return []
    executable = _literal_word(words[0])
    if executable is None or os.path.basename(executable) != "tee":
        return []
    append = False
    options = True
    found: list[RedirectedOutput] = []
    for word_node in words[1:]:
        word = _literal_word(word_node)
        if word is None:
            continue
        if options and word == "--":
            options = False
            continue
        if options and word.startswith("-") and word != "-":
            append = append or word == "--append" or (
                not word.startswith("--") and "a" in word[1:]
            )
            continue
        path = _output_path(word_node, shell_directory)
        if path is not None:
            found.append(RedirectedOutput(path, append))
    return found


def _change_directory(
    command: bashlex.ast.node,
    shell_directory: ShellDirectory,
) -> None:
    words = _word_nodes(command)
    if not words or _literal_word(words[0]) != "cd":
        return
    if len(words) != 2:
        shell_directory.known = False
        return
    target = _literal_word(words[1])
    if target is None or target == "-" or target.startswith("-"):
        shell_directory.known = False
    elif os.path.isabs(target):
        shell_directory.path = os.path.normpath(target)
        shell_directory.known = True
    elif shell_directory.known:
        shell_directory.path = os.path.normpath(
            os.path.join(shell_directory.path, target)
        )


def _walk(
    node: bashlex.ast.node,
    shell_directory: ShellDirectory,
    found: list[RedirectedOutput],
) -> None:
    kind = getattr(node, "kind", None)
    if kind == "command":
        found.extend(_redirects(node, shell_directory))
        found.extend(_tee_outputs(node, shell_directory))
        nested_words = _word_nodes(node) + [
            part.output
            for part in getattr(node, "parts", ())
            if getattr(part, "kind", None) == "redirect"
            and getattr(getattr(part, "output", None), "kind", None) == "word"
        ]
        for word in nested_words:
            for part in getattr(word, "parts", ()):
                nested = getattr(part, "command", None)
                if nested is not None:
                    _walk(nested, shell_directory.copy(), found)
        _change_directory(node, shell_directory)
        return
    if kind == "pipeline":
        for part in node.parts:
            if getattr(part, "kind", None) != "pipe":
                _walk(part, shell_directory.copy(), found)
        return
    if kind == "compound":
        items = getattr(node, "list", ())
        first_word = next(
            (getattr(item, "word", None) for item in items if item.kind == "reservedword"),
            None,
        )
        scoped = shell_directory.copy() if first_word == "(" else shell_directory
        for item in items:
            if getattr(item, "kind", None) != "reservedword":
                _walk(item, scoped, found)
        return
    for part in getattr(node, "parts", ()):
        if getattr(part, "kind", None) not in {"operator", "pipe", "reservedword"}:
            _walk(part, shell_directory, found)


def redirected_outputs(command: str, working_directory: str | None) -> tuple[RedirectedOutput, ...]:
    """Return each concrete file that receives output from this command."""
    try:
        roots = bashlex.parse(command)
    except (bashlex.errors.ParsingError, NotImplementedError):
        return ()
    found: list[RedirectedOutput] = []
    shell_directory = ShellDirectory(working_directory or os.getcwd())
    for root in roots:
        _walk(root, shell_directory, found)

    # One command can truncate and then append to the same file. Follow that
    # file once, from the state before the command. Truncation takes priority.
    unique: list[RedirectedOutput] = []
    for output in found:
        previous_index = next(
            (index for index, previous in enumerate(unique) if previous.path == output.path),
            None,
        )
        if previous_index is None:
            unique.append(output)
        elif unique[previous_index].append and not output.append:
            unique[previous_index] = RedirectedOutput(output.path, False)
    return tuple(unique)

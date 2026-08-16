"""Terminal-owned command formatting and syntax highlighting."""

from __future__ import annotations

import re

from pygments.lexers import BashLexer
from pygments.lexers import get_lexer_for_filename

from terminal.models import RGB
from terminal.mirror.blocks import TerminalStyle, TerminalText

KEYWORD = RGB(198, 120, 221)
BUILTIN = RGB(86, 182, 194)
FUNCTION = RGB(97, 175, 239)
STRING = RGB(152, 195, 121)
VARIABLE = RGB(229, 192, 123)
NUMBER = RGB(209, 154, 102)
COMMENT = RGB(92, 99, 112)
DEFAULT = RGB(171, 178, 191)

SEPARATORS = {";", ";;", "|", "||", "|&", "&", "&&", "(", "{", "!", "$(", "`"}
COMMAND_KEYWORDS = {"do", "then", "else", "elif", "if", "while", "until", "time"}
COMMAND_WORD = re.compile(r"^[\w./@:+-]+$")
BLOCK_OPEN = ("do", "then")
BLOCK_CLOSE = ("done", "fi")


def _color(token_type: object, command: bool = False) -> RGB:
    name = str(token_type)
    if name.startswith("Token.Comment"):
        return COMMENT
    if name.startswith(("Token.Literal.String", "Token.String")):
        return STRING
    if name.startswith("Token.Keyword"):
        return KEYWORD
    if name.startswith("Token.Name.Builtin"):
        return BUILTIN
    if command:
        return FUNCTION
    if name.startswith("Token.Name.Function"):
        return FUNCTION
    if name.startswith("Token.Name.Variable"):
        return VARIABLE
    if name.startswith(("Token.Literal.Number", "Token.Number")):
        return NUMBER
    if name.startswith(("Token.Operator", "Token.Punctuation")):
        return BUILTIN
    return DEFAULT


def format_command(command: str) -> str:
    """Expand a dense Bash one-liner into its existing readable block shape."""
    if "\n" in command or "<<" in command or ";;" in command:
        return command.rstrip("\n")
    tokens = [(str(token_type), value) for token_type, value in BashLexer().get_tokens(command)]
    lines: list[str] = []
    current: list[str] = []
    depth = 0
    continuation = 0
    dedent = 0
    preserve_depth = False
    changed = False

    def emit() -> None:
        nonlocal current, dedent
        text = "".join(current).strip()
        current = []
        if text:
            lines.append("  " * max(0, depth - dedent + continuation) + text)
        dedent = 0

    for token_name, value in tokens:
        stripped = value.strip()
        if not stripped or token_name.startswith(("Token.Literal.String", "Token.String", "Token.Comment")):
            current.append(value)
            continue
        if stripped in ("&&", "||", "|"):
            current.append(stripped)
            emit()
            continuation = 1
            changed = True
            continue
        if stripped == ";":
            emit()
            continuation = 0
            changed = True
            continue
        command_position = not "".join(current).strip()
        if not (token_name.startswith("Token.Keyword") and command_position):
            current.append(value)
            continue
        if stripped in BLOCK_CLOSE + ("else", "elif") and depth == 0:
            current.append(value)
            continue
        if stripped in BLOCK_OPEN:
            emit()
            continuation = 0
            changed = True
            output_depth = depth - 1 if stripped == "then" and preserve_depth else depth
            lines.append("  " * max(0, output_depth) + stripped)
            if not (stripped == "then" and preserve_depth):
                depth += 1
            preserve_depth = False
            continue
        if stripped in BLOCK_CLOSE:
            emit()
            continuation = 0
            changed = True
            depth = max(0, depth - 1)
            lines.append("  " * depth + stripped)
            continue
        if stripped == "else":
            emit()
            continuation = 0
            changed = True
            lines.append("  " * max(0, depth - 1) + stripped)
            continue
        if stripped == "elif":
            emit()
            continuation = 0
            changed = True
            dedent = 1
            preserve_depth = True
        current.append(value)
    emit()
    return "\n".join(lines) if changed and lines else command.rstrip("\n")


def highlighted_lines(command: str) -> tuple[tuple[TerminalText, ...], ...]:
    lines = []
    for line in format_command(command).splitlines() or [""]:
        raw_tokens = list(BashLexer().get_tokens(line))
        rendered = []
        expects_command = True
        for token_index, (token_type, value) in enumerate(raw_tokens):
            if value == "\n":
                continue
            stripped = value.strip()
            # `is_command`, not `command`: this flag used to reuse the name of
            # the str parameter above it. It worked only because the outer
            # loop's iterable — format_command(command) — is evaluated once,
            # before the first rebind; anything later in the function that
            # reached for the command TEXT would have found a bool instead.
            is_command = False
            if stripped:
                if stripped in SEPARATORS:
                    expects_command = True
                elif str(token_type).startswith("Token.Keyword"):
                    expects_command = stripped in COMMAND_KEYWORDS
                elif expects_command and COMMAND_WORD.fullmatch(stripped):
                    following = "".join(part for _, part in raw_tokens[token_index + 1:]).lstrip()
                    if not following.startswith("="):
                        is_command = True
                        expects_command = False
                else:
                    expects_command = False
            rendered.append(TerminalText(value, TerminalStyle(_color(token_type, is_command))))
        lines.append(tuple(rendered))
    return tuple(lines)


def highlighted_source(text: str, path: str, background: RGB | None = None) -> tuple[TerminalText, ...]:
    """Highlight one source fragment using the edited file's lexer."""
    try:
        tokens = get_lexer_for_filename(path).get_tokens(text)
    except Exception:
        return (TerminalText(text, TerminalStyle(DEFAULT, background=background)),)
    return tuple(
        TerminalText(value, TerminalStyle(_color(token_type), background=background))
        for token_type, value in tokens
        if value != "\n"
    )

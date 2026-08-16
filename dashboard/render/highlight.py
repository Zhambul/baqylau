"""Dashboard-owned syntax highlighting for fenced source blocks."""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    # pygments is an OPTIONAL runtime dependency — the two functions below
    # import it inside their own try/except so that a machine without it simply
    # renders unhighlighted. The checker still needs the Lexer type to know
    # what the cache holds, and this is the one import form that gives it that
    # without making the package required at runtime.
    from pygments.lexer import Lexer


COLORS = {
    "keyword": "\033[38;2;198;120;221m",
    "builtin": "\033[38;2;86;182;194m",
    "function": "\033[38;2;97;175;239m",
    "string": "\033[38;2;152;195;121m",
    "variable": "\033[38;2;229;192;123m",
    "number": "\033[38;2;209;154;102m",
    "punctuation": "\033[38;2;86;182;194m",
    "comment": "\033[38;2;92;99;112m",
    "text": "\033[38;2;171;178;191m",
}
RESET = "\033[0m"
_lexers: dict[str, Lexer] = {}   # language name or file path -> lexer (cached)


def _color(token_type) -> str:
    name = str(token_type)
    if name.startswith("Token.Comment"):
        return COLORS["comment"]
    if name.startswith(("Token.Literal.String", "Token.String")):
        return COLORS["string"]
    if name.startswith("Token.Keyword"):
        return COLORS["keyword"]
    if name.startswith("Token.Name.Builtin"):
        return COLORS["builtin"]
    if name.startswith("Token.Name.Function"):
        return COLORS["function"]
    if name.startswith("Token.Name.Variable"):
        return COLORS["variable"]
    if name.startswith(("Token.Literal.Number", "Token.Number")):
        return COLORS["number"]
    if name.startswith(("Token.Operator", "Token.Punctuation")):
        return COLORS["punctuation"]
    return COLORS["text"]


def source_ansi(text: str, language: str) -> str | None:
    """Return highlighted ANSI or ``None`` when highlighting is unavailable."""
    body = text.rstrip("\n")
    if not body.strip():
        return None
    try:
        from pygments.lexers import get_lexer_by_name  # noqa: PLC0415 — optional dep, inside its own try/except

        lexer = _lexers.get(language)
        if lexer is None:
            lexer = _lexers[language] = get_lexer_by_name(language)
        rendered = "".join(_color(token_type) + value for token_type, value in lexer.get_tokens(body))
        return rendered.rstrip("\n") + RESET
    except Exception:
        return None


def source_ansi_for_path(text: str, path: str) -> str | None:
    """Return highlighted ANSI using the lexer inferred from a file path."""
    body = text.rstrip("\n")
    if not body:
        return ""
    try:
        from pygments.lexers import get_lexer_for_filename  # noqa: PLC0415 — optional dep, inside its own try/except

        lexer = _lexers.get(path)
        if lexer is None:
            lexer = _lexers[path] = get_lexer_for_filename(path)
        rendered = "".join(_color(token_type) + value for token_type, value in lexer.get_tokens(body))
        return rendered.rstrip("\n") + RESET
    except Exception:
        return None

# Copyright (c) 2026 Zhambyl Yermagambet
"""Reply to terminal queries from a program in a pseudo-terminal."""

import dataclasses


@dataclasses.dataclass(frozen=True)
class _TerminalQuery:
    query: bytes
    reply: bytes


@dataclasses.dataclass(frozen=True, order=True)
class _LocatedReply:
    position: int
    reply: bytes


# No reply to the progressive keyboard query (`CSI ? u`): this emulator does not
# support that protocol, and a program learns that from the DA1 reply that comes
# alone. A false reply reached a harness as a typed `u` in its input.
_QUERY_REPLIES = (
    _TerminalQuery(b"\x1b[c", b"\x1b[?1;2c"),
    _TerminalQuery(b"\x1b]10;?\x07", b"\x1b]10;rgb:ffff/ffff/ffff\x1b\\"),
    _TerminalQuery(b"\x1b]10;?\x1b\\", b"\x1b]10;rgb:ffff/ffff/ffff\x1b\\"),
    _TerminalQuery(b"\x1b]11;?\x07", b"\x1b]11;rgb:0000/0000/0000\x1b\\"),
    _TerminalQuery(b"\x1b]11;?\x1b\\", b"\x1b]11;rgb:0000/0000/0000\x1b\\"),
)
_CURSOR_POSITION_QUERY = b"\x1b[6n"


@dataclasses.dataclass
class TerminalQueryResponder:
    """Reply to terminal queries that need input from the emulator."""

    pending: bytes = b""

    def feed(self, chunk: bytes, row: int, column: int) -> bytes:
        """Return all replies in the order of their queries.

        Returns:
            Terminal query replies.

        """
        buffered_input = self.pending + chunk
        found = _located_replies(buffered_input, row, column)
        self.pending = _pending_query_prefix(buffered_input)
        return b"".join(located.reply for located in sorted(found))


def _located_replies(
    buffered_input: bytes,
    row: int,
    column: int,
) -> list[_LocatedReply]:
    found: list[_LocatedReply] = []
    for terminal_query in _QUERY_REPLIES:
        found.extend(_query_locations(buffered_input, terminal_query))
    cursor_reply = f"\x1b[{row};{column}R".encode()
    found.extend(
        _query_locations(
            buffered_input,
            _TerminalQuery(_CURSOR_POSITION_QUERY, cursor_reply),
        ),
    )
    return found


def _query_locations(
    buffered_input: bytes,
    terminal_query: _TerminalQuery,
) -> list[_LocatedReply]:
    found: list[_LocatedReply] = []
    position = buffered_input.find(terminal_query.query)
    while position >= 0:
        found.append(_LocatedReply(position, terminal_query.reply))
        position = buffered_input.find(
            terminal_query.query,
            position + len(terminal_query.query),
        )
    return found


def _pending_query_prefix(buffered_input: bytes) -> bytes:
    queries = (
        *(terminal_query.query for terminal_query in _QUERY_REPLIES),
        _CURSOR_POSITION_QUERY,
    )
    longest_prefix = max(len(query) for query in queries) - 1
    tail = buffered_input[-longest_prefix:]
    for length in range(len(tail), 0, -1):
        candidate = tail[-length:]
        if any(query.startswith(candidate) for query in queries):
            return candidate
    return b""

"""What this application raises when the CALLER is wrong.

Three ways a request can be answerable-with-a-reason, and nothing else in this
file. Every one of them is the caller's to fix, which is what makes the message
each carries safe to put on the wire — the api/ layer renders any of them as its
400 (api/app.py).

THE DISTINCTION THAT MATTERS is against a bare `ValueError`, `KeyError` or
`TypeError`. Those are raised in a hundred places in this tree and almost all of
them are invariant checks on code we wrote — "send_text handler requires
SendText", "session has no attached harness plugin", a translator disagreeing
with itself. An HTTP layer that mapped the BUILTIN types to 400 answered every
one of those internal bugs with "bad input": no `errors` row, no 500, and the
internal message on the wire. Raising one of these instead is how a call site
says "I mean it"; everything else stays a bug and is audited as one.

They subclass the builtin whose SHAPE they have (the same thing
`UnknownHookHarness(LookupError)` does), so a caller that already catches
narrowly keeps working. The api/ layer registers handlers on
`ApplicationInputError` alone — the builtin is a description, never the contract.
"""

from __future__ import annotations


class ApplicationInputError(Exception):
    """Base: the request cannot be served, and the reason is the request."""


class UnknownReference(ApplicationInputError, LookupError):
    """An identifier in the request names nothing this application holds.

    An unknown session id, a content reference no canonical event carries, a
    harness that is not installed.
    """


class UnsupportedRequest(ApplicationInputError, TypeError):
    """Well-formed, and the target exists — but it cannot be served this way.

    Asking for a field's text when the field holds something that is not text.
    """

"""Facts in, markup out. Nothing here reads a store, a service, or a session.

    ansi.py       ANSI/SGR -> HTML, and the html.escape() core every byte
                  that reaches the page passes through
    highlight.py  syntax colour for fenced source
    markdown.py   a small, safe Markdown subset
    diff.py       a unified diff, line-numbered
    items/        one canonical activity -> one item the browser draws

This tier is reusable precisely because it is inert: `api/common/content.py`
renders a file with the same functions the session page uses, and a test can
call any of them with a literal.
"""

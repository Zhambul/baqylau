"""What the browser asks for, answered once per question.

    models.py     the shapes a page receives
    sessions.py   what a session IS: the list, and one snapshot at one cursor
    activity.py   the backlog, a block at a time
    streams.py    the live feed: one cursor in, one frame out
    overview.py   the list page's own state — prefs, usage, push, presence
    workspace.py  one session's unsent work — drafts, queue, dialog choices
    notices.py    the newest thing worth telling you about

The split that matters here is between what the SESSION did (folded from
canonical facts by the engine, and merely arranged here) and what YOU did in
the browser (owned by `dashboard/prefs/`, and true only for you). A page reads
both and cannot tell them apart; nothing below this tier may confuse them.
"""

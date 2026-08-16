"""The web dashboard: the browser surface and everything behind it.

    static/     the single-page app itself
    render/     facts -> markup, and nothing else
    services/   one question, one answer — what a page asks the daemon for
    prefs/      what YOU chose, per key, durable across sessions and devices
    config.py   the knobs the dashboard owns
    paths.py    the files it owns
    cli.py      serve · start · stop · status
    dictate.py  the Deepgram grant the browser needs to dictate

Things that were here and belong elsewhere: alerts (a concern of their own, in
`notify/`), the pasteboard (a machine fact, in `core/`), and the HTTP endpoints
(`api/dashboard/`, which imports this package and not the reverse).
"""

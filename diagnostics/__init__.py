"""The operational diagnostic database, whole: one file, one owner.

Harness facts — raw observations, translations, canonical events, provenance —
live in `events.db` and belong to `engine/`. Everything recorded HERE is
application mechanics instead: a swallowed exception, a state file we wrote, a
child we spawned, a stream that opened and closed, a gesture the browser tried.
Not a fact about a session; a fact about us.

    database.py   where the file lives, how it is opened, what tables it has
    record.py     the writers — the one API every process in the tree calls
    read.py       the typed reads the dashboard renders
    telemetry.py  browser-reported observations, funnelled into the writers

Split across three packages before: written by `core/`, read by `app/`,
surfaced by `dashboard/`. One database deserves one directory.
"""

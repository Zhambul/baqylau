"""The operational diagnostic database, whole: one file, one owner.

Harness facts — raw observations, translations, canonical events, provenance —
live in `main.db` and belong to `domain/` and `repository/`. Everything recorded
HERE is application mechanics instead: a swallowed exception, a state file we
wrote, a child we spawned, a stream that opened and closed, a gesture the
browser tried. Not a fact about a session; a fact about us.

    models.py     the vocabulary, as types
    record.py     the writers — the free-function facade every process calls
    telemetry.py  browser-reported observations, funnelled into the writers

The STORAGE is `repository/impl/sqlite/diagnostics.py`, like everything else.
What is left here is the shape of the records and the one facade that lets a
hook process write one from inside an `except` block.
"""

"""Reads that need nothing but the facts.

    content.py   a frontend's content reference resolved back to its event

A query that also needs git, the diagnostic database, or a terminal is not a
fact read — it composes concerns, and lives a tier up in `app/services/`.

`evidence.py` is gone: it joined four tables it owned none of, and its work is
`TranslationEvidenceRepository` now — in the layer that owns them, and in four
queries rather than four per event.
"""

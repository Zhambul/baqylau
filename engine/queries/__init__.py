"""Reads that need nothing but the facts.

    content.py   a frontend's content reference resolved back to its event

A query that also needs git, the audit database, or a terminal is not a
fact read — it composes concerns, and lives a tier up in `app/services/`.

The old forensic join is gone from this layer: its work is
`RawEventAuditRepository` now — in the layer that owns them, and in four
queries rather than four per event.
"""

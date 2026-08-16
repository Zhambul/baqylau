"""Where facts live: one database, one owner per table.

    database.py   the connection policy and the schema, for everyone below
    recorder.py   append-only raw evidence — the one write API observers use
    canonical.py  raw evidence's stored interpretations, written transactionally
    sessions.py   the `sessions` table: one writer, one write method
    output.py     operation output files, located by facts and read to the end

Every write in the system passes through here; nothing here interprets.
"""

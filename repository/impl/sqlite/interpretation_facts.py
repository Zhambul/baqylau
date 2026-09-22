# Copyright (c) 2026 Zhambyl Yermagambet
"""Recheck complete cause references inside the canonical transaction."""

import sqlite3
from graphlib import TopologicalSorter

from baqylau_extension_api.models.canonical import CanonicalFact

from extensions.models.interpretation_facts import (
    cause_graph,
    require_same_identity as require_same_identity,
    validate_fact as validate_fact,
)


def validate_causes(
    connection: sqlite3.Connection, history_revision: str, proposed: tuple[CanonicalFact, ...],
) -> None:
    """Allow retained intermediate causes but reject missing parents and cycles."""
    known = {fact.event_id for fact in proposed}
    graph = cause_graph(proposed)
    tuple(TopologicalSorter(graph).static_order())
    causes = set().union(*graph.values()) if graph else set()
    for cause in causes - known:
        _require_cause(connection, history_revision, cause)


def _require_cause(connection: sqlite3.Connection, history_revision: str, cause: str) -> None:
    row = connection.execute(
        "SELECT 1 FROM raw_events WHERE raw_event_id=? UNION ALL "
        "SELECT 1 FROM canonical_events WHERE history_revision=? AND event_id=? LIMIT 1",
        (cause, history_revision, cause),
    ).fetchone()
    if row is None:
        message = "canonical cause is not recorded in the selected history or interpretation"
        raise ValueError(message)

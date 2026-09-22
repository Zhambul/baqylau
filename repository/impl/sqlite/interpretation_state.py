# Copyright (c) 2026 Zhambyl Yermagambet
"""Read and compare decoder state inside complete interpretation transactions."""

import sqlite3

from baqylau_extension_api.models.documents import EncodedDocument
from baqylau_extension_api.models.translation_inputs import TranslationState

from extensions.models.interpretation_reads import TranslationStateKey


def read_state(connection: sqlite3.Connection, key: TranslationStateKey) -> TranslationState:
    """Represent a missing state row as the explicit initial revision.

    Returns:
        The exact prior state, including an intentional empty document.

    """
    row = connection.execute(
        "SELECT revision, document FROM extension_translation_state "
        "WHERE extension_id=? AND history_revision=? AND scope=? AND source_identity=?",
        _key_columns(key),
    ).fetchone()
    if row is None:
        return TranslationState(revision=0)
    encoded = row["document"]
    document = None if encoded is None else EncodedDocument.model_validate_json(encoded)
    return TranslationState(revision=int(row["revision"]), document=document)


def write_state(
    connection: sqlite3.Connection, key: TranslationStateKey,
    expected: TranslationState, document: EncodedDocument | None,
) -> None:
    """Compare the complete captured state, then advance it with the interpretation.

    Raises:
        ValueError: If another interpretation has changed the decoder state.

    """
    if read_state(connection, key) != expected:
        message = "interpretation decoder state is stale"
        raise ValueError(message)
    encoded = None if document is None else document.model_dump_json()
    connection.execute(
        "INSERT INTO extension_translation_state(extension_id, history_revision, scope, source_identity, "
        "revision, document) "
        "VALUES(?, ?, ?, ?, ?, ?) ON CONFLICT(extension_id, history_revision, scope, source_identity) "
        "DO UPDATE SET revision=excluded.revision, document=excluded.document",
        (*_key_columns(key), expected.revision + 1, encoded),
    )


def _key_columns(key: TranslationStateKey) -> tuple[str, ...]:
    return key.extension_id, key.history_revision, key.scope.model_dump_json(), key.source_identity

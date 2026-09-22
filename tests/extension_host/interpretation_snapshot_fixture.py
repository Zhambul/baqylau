# Copyright (c) 2026 Zhambyl Yermagambet
"""Build read fixtures with the production codec, without claiming worker acceptance."""

from pathlib import Path

from baqylau_extension_api.models import canonical, events, scopes

from extensions.models.interpretation_snapshot import PriorStateRequest
from repository.impl.sqlite import databases, interpretation_codec
from repository.impl.sqlite.interpretations import SqliteInterpretationRepository
from tests.extension_api import samples

INSTALLATION = scopes.InstallationScope()


def repository(path: Path) -> SqliteInterpretationRepository:
    """Use a private current-schema database.

    Returns:
        The production mixed fact repository.

    """
    return SqliteInterpretationRepository(databases.main_database(str(path / "main.db")))


def request(
    store: SqliteInterpretationRepository, scope: scopes.ExtensionScope = INSTALLATION,
) -> PriorStateRequest:
    """Select the current live head and one exact scope.

    Returns:
        A bounded request with the production default limits.

    """
    return PriorStateRequest(
        history_revision="default", scope=scope, expected_canonical_cursor=store.current_fact_page(0, 1).head,
    )


def fact(
    identity: str, scope: scopes.ExtensionScope = INSTALLATION, encoded: str = '"hello"',
) -> events.ExtensionFact:
    """Use valid public fields and a text document.

    Returns:
        A distinct fact for storage read checks.

    """
    return events.ExtensionFact(
        event_id=identity, event_type="test.reader.created", scope=scope,
        document=samples.encoded_document(encoded),
    )


def seed(
    store: SqliteInterpretationRepository, facts: tuple[canonical.CanonicalFact, ...], history: str = "default",
) -> None:
    """Write read fixtures in one transaction, without a processing verdict."""
    with store.database.write() as connection:
        connection.execute("INSERT OR IGNORE INTO canonical_histories VALUES(?, 1000.0)", (history,))
        for selected in facts:
            interpretation_codec.insert_fact(connection, selected, history, 1000.0)


def wire_size(snapshot: canonical.CoreStateSnapshot) -> int:
    """Measure complete encoded bytes, including multibyte text and escaping.

    Returns:
        The actual UTF-8 size.

    """
    encoded = snapshot.model_dump_json().encode("utf-8")
    return len(encoded)

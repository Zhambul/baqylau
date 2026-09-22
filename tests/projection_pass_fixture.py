# Copyright (c) 2026 Zhambyl Yermagambet
"""Build local projector packages and captured fact pages for pass tests."""

from __future__ import annotations

from dataclasses import dataclass

from baqylau_extension_api.manifest import data
from baqylau_extension_api.models import events, projections, records, scopes
from baqylau_extension_api.models.projection_entries import ProjectedEntry
from baqylau_extension_api.models.record_changes import PutRecord

from extensions.models import interpretation_reads
from extensions.models.interpretations import StoredCanonicalFact
from tests.extension_api import operation_samples, projection_samples, source_samples

OWNER = operation_samples.OWNER
ENTRY_TYPE = projection_samples.ENTRY_TYPE
MANIFEST = projection_samples.manifest()
TRANSFORM_SCOPES: data.ScopeKinds = ("session", "workspace", "repository", "installation")
TRANSFORM_MANIFEST = MANIFEST.model_copy(update={
    "capabilities": (*MANIFEST.capabilities, "projection_transformer"),
    "contributions": MANIFEST.contributions.model_copy(update={
        "processing": (*MANIFEST.contributions.processing, data.ProcessingSelection(
            capability="projection_transformer",
            scopes=TRANSFORM_SCOPES,
            input_types=(source_samples.EVENT_TYPE,),
        )),
    }),
})
SCOPE = scopes.SessionScope.model_validate_json(
    '{"kind":"session","session_id":"session-one","actor_id":"lead","harness":"codex"}',
)
FACT_ID = "fact-one"
FACT_CURSOR = 3
EXTRA_KEY = "extra"


def fact(event_id: str, cursor: int, encoded: str = '"first"') -> StoredCanonicalFact:
    """Build one stored extension fact at an explicit accepted cursor.

    Returns:
        The stored fact.

    """
    return StoredCanonicalFact(
        fact=events.ExtensionFact(
            event_id=event_id,
            scope=SCOPE,
            event_type=source_samples.EVENT_TYPE,
            document=operation_samples.query_request(encoded).arguments,
            causes=(),
        ),
        cursor=cursor,
        accepted_at=float(cursor),
        history_revision="default",
    )


@dataclass(frozen=True)
class FakeFacts:
    """Serve hand-built stored facts for one scope."""

    stored: tuple[StoredCanonicalFact, ...]

    def facts_for_scope(
        self, history_revision: str, scope: scopes.ExtensionScope, after_cursor: int, limit: int,
    ) -> interpretation_reads.CanonicalPage:
        """Read the selected scope page after a cursor.

        Returns:
            The bounded ordered page.

        """
        selected = tuple(
            stored for stored in self.stored
            if stored.cursor > after_cursor and stored.fact.scope == scope
        )[:limit]
        return interpretation_reads.CanonicalPage(
            history_revision=history_revision,
            head=max((stored.cursor for stored in self.stored), default=0),
            facts=selected,
        )


def record_key(scope: scopes.ExtensionScope, owner: str = OWNER) -> records.RecordKey:
    """Build the package's current-state record key.

    Returns:
        The owned record key.

    """
    return records.RecordKey(owner=owner, collection=f"{owner}.records", scope=scope, key="current")


@dataclass(frozen=True)
class LocalProjector:
    """Project one fact into two entries and one record change."""

    def select_records(
        self, selection_request: projections.ProjectionSelectionRequest,
    ) -> projections.ProjectionReadSet:
        """Select the package's current-state key.

        Returns:
            The complete read set.

        """
        key = record_key(selection_request.binding.context.scope)
        return projections.ProjectionReadSet(binding=selection_request.binding, keys=(key,))

    def project(self, projection_request: projections.ProjectionRequest) -> projections.ProjectionResult:
        """Propose two ordered entries and one record put.

        Returns:
            The complete projection result.

        """
        stored = projection_request.events[-1]
        fact_value = stored.fact
        assert isinstance(fact_value, events.ExtensionFact)
        key = record_key(projection_request.binding.context.scope)
        return projections.ProjectionResult(
            binding=projection_request.binding,
            entries=tuple(
                ProjectedEntry(
                    entry_key=entry_key,
                    source_event_id=fact_value.event_id,
                    entry_type=ENTRY_TYPE,
                    document=fact_value.document,
                    summary=f"Card {entry_key}",
                    occurred_at=float(stored.cursor),
                )
                for entry_key in ("summary", "detail")
            ),
            record_changes=(
                PutRecord(
                    key=key,
                    expected_revision=projection_request.prior_records[0].revision,
                    document=fact_value.document,
                    summary="Card",
                ),
            ),
        )


@dataclass(frozen=True)
class ForeignKeyProjector:
    """Select a record key owned by another package."""

    def select_records(
        self, selection_request: projections.ProjectionSelectionRequest,
    ) -> projections.ProjectionReadSet:
        """Select a foreign key.

        Returns:
            The complete read set.

        """
        key = record_key(selection_request.binding.context.scope, owner="test.other")
        return projections.ProjectionReadSet(binding=selection_request.binding, keys=(key,))

    def project(self, projection_request: projections.ProjectionRequest) -> projections.ProjectionResult:
        """Return an empty result.

        Returns:
            The complete projection result.

        """
        return projections.ProjectionResult(binding=projection_request.binding)


@dataclass(frozen=True)
class ForeignEntryProjector:
    """Name a fact outside the request."""

    def select_records(
        self, selection_request: projections.ProjectionSelectionRequest,
    ) -> projections.ProjectionReadSet:
        """Select no record keys.

        Returns:
            The complete read set.

        """
        return projections.ProjectionReadSet(binding=selection_request.binding)

    def project(self, projection_request: projections.ProjectionRequest) -> projections.ProjectionResult:
        """Return one entry for a fact outside the request.

        Returns:
            The complete projection result.

        """
        return projections.ProjectionResult(
            binding=projection_request.binding,
            entries=(ProjectedEntry(
                entry_key="summary",
                source_event_id="missing-fact",
                entry_type=ENTRY_TYPE,
                document=operation_samples.query_request('"x"').arguments,
                summary="Card",
            ),),
        )

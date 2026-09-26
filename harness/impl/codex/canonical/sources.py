# Copyright (c) 2026 Zhambyl Yermagambet
"""Collect Codex rollout readers for active sessions."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING

from domain import ids as domain_ids, messaging
from harness import contract as harness_contract
from harness.impl.codex import ids_session, ids_session_types
from harness.impl.codex.canonical import (
    rollout,
    source_cache,
    source_catalog,
    source_groups,
    source_readers,
    title as native_title,
)
from harness.models import raw_events, session as session_models

if TYPE_CHECKING:
    from collections.abc import MutableMapping


class CodexRawEventSources(harness_contract.HarnessRawEventSources):
    """Collect rollout and native-title readers for active sessions."""

    def __init__(
        self,
        configuration_directory: str,
        title_repository: native_title.CodexThreadTitleRepository | None = None,
    ) -> None:
        """Initialize the Codex source collection."""
        self._catalog = source_catalog.RolloutCatalog(configuration_directory)
        self._titles = title_repository or native_title.CodexThreadTitleRepository(
            configuration_directory,
        )
        self._known_rollout_paths: frozenset[str] = frozenset()
        self._pending_rollouts: list[source_groups.PendingRollout] = []
        self._child_parent_by_path: dict[str, ids_session_types.CodexSessionId] = {}
        self._child_rollouts: list[source_groups.ChildRollouts] = []
        self._sessions: dict[domain_ids.SessionId, source_cache.CodexSessionSources] = {}
        self._child_sources: dict[
            tuple[domain_ids.SessionId, str, messaging.ActorRole],
            source_readers.CodexRolloutRawEventSource,
        ] = {}

    def release_session(self, session_id: domain_ids.SessionId) -> None:
        """Release rollout readers for one finished session."""
        self._sessions.pop(session_id, None)
        for key in tuple(self._child_sources):
            if key[0] == session_id:
                self._child_sources.pop(key, None)

    def for_session(
        self,
        session: session_models.Session,
    ) -> tuple[harness_contract.HarnessRawEventSource, ...]:
        """Return the sources for one session.

        Returns:
            The sources for one session.

        """
        cached = self._sessions.get(session.session_id)
        if cached is None or cached.source_reference != session.source_reference:
            cached = source_cache.codex_session_sources(session, self._titles)
            self._sessions[session.session_id] = cached
        session_sources = list(cached.sources)
        for child_path in self._child_rollout_paths(
            ids_session.codex_session_id_from_domain(session.session_id),
        ):
            child_source = self._child_source(session, cached, child_path)
            if child_source is not None:
                session_sources.append(child_source)
        if cached.owns_lead_session:
            session_sources.extend(_page_sources(self._child_sources, session, self._known_rollout_paths))
        return tuple(session_sources)

    def _child_rollout_paths(
        self,
        parent_codex_session_id: ids_session_types.CodexSessionId,
    ) -> tuple[str, ...]:
        self._refresh_child_rollouts()
        selected_children = next(
            (child for child in self._child_rollouts if child.parent_session_id == parent_codex_session_id),
            None,
        )
        if selected_children is None or not selected_children.paths:
            return ()
        return tuple(selected_children.paths)

    def _refresh_child_rollouts(self) -> None:
        rollout_paths = frozenset(self._catalog.paths())
        removed = self._known_rollout_paths - rollout_paths
        added = rollout_paths - self._known_rollout_paths
        self._pending_rollouts = source_groups.updated_pending_rollouts(
            self._pending_rollouts,
            removed,
            added,
        )
        self._child_parent_by_path = source_cache.without_removed_parents(
            self._child_parent_by_path,
            removed,
        )
        self._child_sources = source_cache.without_removed_sources(
            self._child_sources,
            removed,
        )
        changed = bool(removed or added)
        changed = self._reconcile_pending_rollouts() or changed
        self._known_rollout_paths = rollout_paths
        if changed:
            self._child_rollouts = source_groups.child_rollout_groups(
                self._child_parent_by_path,
            )

    def _reconcile_pending_rollouts(self) -> bool:
        changed = False
        for pending in tuple(self._pending_rollouts):
            marker = source_cache.file_marker(pending.path)
            if marker == pending.marker:
                continue
            pending.marker = marker
            metadata = source_catalog.session_metadata(pending.path)
            if metadata is None:
                continue
            self._pending_rollouts.remove(pending)
            parent_session_id = source_catalog.parent_thread_id(metadata)
            if parent_session_id:
                self._child_parent_by_path[pending.path] = ids_session_types.CodexSessionId(
                    parent_session_id,
                )
                changed = True
        return changed

    def _child_source(
        self,
        session: session_models.Session,
        cached: source_cache.CodexSessionSources,
        child_path: str,
    ) -> source_readers.CodexRolloutRawEventSource | None:
        child_body_position = rollout.subagent_body_offset(child_path)
        if child_body_position == 0:
            return None
        actor_role = messaging.ActorRole.CHILD if cached.owns_lead_session else messaging.ActorRole.SIDECAR
        key = session.session_id, child_path, actor_role
        child_source = self._child_sources.get(key)
        if child_source is None:
            child_source = source_readers.CodexRolloutRawEventSource(
                raw_events.RawEventSourceContext(
                    session_id=session.session_id,
                    lead_actor_id=session.lead_actor_id,
                    actor_id=ids_session.actor_id_from_codex(
                        ids_session_types.CodexActorId(
                            source_catalog.codex_session_id(child_path),
                        ),
                    ),
                    parent_actor_id=session.lead_actor_id,
                    source_reference=child_path,
                ),
                child_body_position,
                actor_role,
            )
            self._child_sources[key] = child_source
        return child_source


def _page_sources(
    sources: MutableMapping[
        tuple[domain_ids.SessionId, str, messaging.ActorRole], source_readers.CodexRolloutRawEventSource,
    ],
    session: session_models.Session,
    rollout_paths: frozenset[str],
) -> list[source_readers.CodexRolloutRawEventSource]:
    session_id = ids_session.codex_session_id_from_domain(session.session_id)
    return [_page_source(sources, session, page_path) for page_path in _session_pages(rollout_paths, session_id)]


def _page_source(
    sources: MutableMapping[
        tuple[domain_ids.SessionId, str, messaging.ActorRole], source_readers.CodexRolloutRawEventSource,
    ],
    session: session_models.Session,
    page_path: str,
) -> source_readers.CodexRolloutRawEventSource:
    key = session.session_id, page_path, messaging.ActorRole.LEAD
    page_source = sources.get(key)
    if page_source is None:
        page_source = source_readers.CodexRolloutRawEventSource(
            dataclasses.replace(session.source_context, source_reference=page_path),
        )
        sources[key] = page_source
    return page_source


def _session_pages(
    rollout_paths: frozenset[str], session_id: ids_session_types.CodexSessionId,
) -> tuple[str, ...]:
    # Codex 0.156 continues a session in a new file after a rewind. The file is
    # named `<session id>_<page id>` and starts from the rewind point, so each
    # page is read after the ones before it.
    prefix = f"{session_id}_"
    pages = (path for path in rollout_paths if source_catalog.codex_session_id(path).startswith(prefix))
    return tuple(sorted(pages, key=lambda path: Path(path).name))

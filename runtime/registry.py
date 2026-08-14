"""Concrete harness registration and persisted session/actor ownership lookup."""

from __future__ import annotations

from dataclasses import dataclass

from contracts.harness import HarnessPlugin, RecognizedSession, SessionCandidate
from domain.codec import SCHEMA_VERSION
from domain.ids import SessionId
from runtime.event_store import EventStore


class HarnessRegistryError(RuntimeError):
    pass


class UnsupportedSession(HarnessRegistryError):
    pass


class AmbiguousSession(HarnessRegistryError):
    pass


@dataclass(frozen=True)
class RegisteredSession:
    plugin: HarnessPlugin
    session: RecognizedSession


class HarnessRegistry:
    def __init__(self, event_store: EventStore) -> None:
        self.event_store = event_store
        self._plugins: dict[str, HarnessPlugin] = {}

    def register(self, plugin: HarnessPlugin) -> None:
        name = plugin.info.name.strip()
        if not name:
            raise HarnessRegistryError("harness name cannot be empty")
        if name != plugin.info.name:
            raise HarnessRegistryError("harness name cannot have surrounding whitespace")
        if name in self._plugins:
            raise HarnessRegistryError(f"duplicate harness: {name}")
        if plugin.info.canonical_version != SCHEMA_VERSION:
            raise HarnessRegistryError(
                f"harness {name!r} uses canonical version {plugin.info.canonical_version}, expected {SCHEMA_VERSION}"
            )
        if plugin.info.supports_attachments and plugin.launcher is None:
            raise HarnessRegistryError(
                f"harness {name!r} advertises attachments without a launcher"
            )
        if plugin.info.default_for_launch and plugin.launcher is None:
            raise HarnessRegistryError(
                f"harness {name!r} is the launch default but has no launcher"
            )
        if plugin.info.default_for_launch and any(
            registered.info.default_for_launch for registered in self._plugins.values()
        ):
            raise HarnessRegistryError("multiple harnesses are marked as the launch default")
        self._plugins[name] = plugin

    def validate(self) -> None:
        launchable = [plugin for plugin in self._plugins.values() if plugin.launcher is not None]
        defaults = [plugin for plugin in launchable if plugin.info.default_for_launch]
        if launchable and not defaults:
            raise HarnessRegistryError("no launchable harness is marked as the launch default")

    def plugin(self, harness: str) -> HarnessPlugin:
        try:
            return self._plugins[harness]
        except KeyError as error:
            raise HarnessRegistryError(f"unregistered harness: {harness}") from error

    def plugins(self) -> tuple[HarnessPlugin, ...]:
        return tuple(self._plugins[name] for name in sorted(self._plugins))

    def discover_sessions(self, limit: int | None = None) -> tuple[RegisteredSession, ...]:
        if limit is not None and limit <= 0:
            raise ValueError("session discovery limit must be positive")
        discovered: dict[SessionId, RegisteredSession] = {}
        remaining = [
            (plugin, iter(plugin.sessions.discover()))
            for plugin in self.plugins()
        ]
        while remaining and (limit is None or len(discovered) < limit):
            next_round = []
            for plugin, sessions in remaining:
                if limit is not None and len(discovered) >= limit:
                    break
                try:
                    session = next(sessions)
                except StopIteration:
                    continue
                existing = discovered.get(session.session_id)
                if existing is not None:
                    raise AmbiguousSession(
                        f"session {session.session_id} discovered by "
                        f"{existing.plugin.info.name!r} and {plugin.info.name!r}"
                    )
                self.event_store.register_session(plugin.info.name, session)
                stored_session = self.event_store.recognized_session(session.session_id)
                if stored_session is None:
                    raise HarnessRegistryError(
                        f"registered session disappeared: {session.session_id}"
                    )
                discovered[session.session_id] = RegisteredSession(plugin, stored_session)
                next_round.append((plugin, sessions))
            remaining = next_round
        return tuple(discovered.values())

    def recognize(self, candidate: SessionCandidate) -> RegisteredSession:
        matches: list[RegisteredSession] = []
        for plugin in self.plugins():
            session = plugin.sessions.recognize(candidate)
            if session is not None:
                matches.append(RegisteredSession(plugin, session))
        if not matches:
            raise UnsupportedSession(f"no harness recognized {candidate.source_reference!r}")
        if len(matches) != 1:
            names = ", ".join(match.plugin.info.name for match in matches)
            raise AmbiguousSession(f"multiple harnesses recognized {candidate.source_reference!r}: {names}")
        match = matches[0]
        self.event_store.register_session(match.plugin.info.name, match.session)
        return match

    def plugin_for_session(self, session_id: SessionId) -> HarnessPlugin:
        harness = self.event_store.session_harness(session_id)
        if harness is None:
            raise UnsupportedSession(f"unknown session: {session_id}")
        return self.plugin(harness)

    def registered_session(self, session_id: SessionId) -> RegisteredSession:
        session = self.event_store.recognized_session(session_id)
        if session is None:
            raise UnsupportedSession(f"unknown session: {session_id}")
        return RegisteredSession(self.plugin_for_session(session_id), session)

    def recently_observed_sessions(self, limit: int) -> tuple[RegisteredSession, ...]:
        return tuple(
            self.registered_session(session_id)
            for session_id in self.event_store.recently_observed_session_ids(limit)
        )

    def session_is_finished(self, session_id: SessionId) -> bool:
        return self.event_store.session_is_finished(session_id)

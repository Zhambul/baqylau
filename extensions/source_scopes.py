# Copyright (c) 2026 Zhambyl Yermagambet
"""Combine active actors with explicit host-owned scopes and the installation scope."""

from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from threading import Lock

from baqylau_extension_api.models.scopes import ExtensionScope, InstallationScope, SessionScope
from pydantic import TypeAdapter

from domain.lifecycle import LifecycleState
from extensions.source_scope_contract import ExtensionScopeRegistry, ExtensionSourceScopes
from repository.contract.session_data_protocols import SessionDataAggregateRead

MAX_HELD_SCOPES = 1000


class ActiveExtensionScopes(ExtensionScopeRegistry, ExtensionSourceScopes):
    """Reference-count exact scopes; retain no worker or frontend object."""

    def __init__(self, changed: Callable[[], None]) -> None:
        """Create an installation-only selection with no external I/O."""
        self._changed = changed
        self._counts: Counter[ExtensionScope] = Counter()
        self._lock = Lock()

    def source_scopes(self) -> tuple[ExtensionScope, ...]:
        """Read a stable complete set, not individual view leases.

        Returns:
            The installation scope and distinct explicitly retained scopes.

        """
        with self._lock:
            scopes = {*self._counts, InstallationScope()}
        return tuple(sorted(scopes, key=lambda scope: scope.model_dump_json()))

    @contextmanager
    def hold_scope(self, scope: ExtensionScope) -> Iterator[None]:
        """Keep repository sources active without creating a coding session.

        Yields:
            Nothing; the context itself owns the scope reference.

        Raises:
            ValueError: If the number of distinct explicit scopes exceeds the host bound.

        """
        selected = TypeAdapter[ExtensionScope](ExtensionScope).validate_python(scope)
        if selected.kind == "installation":
            yield
            return
        with self._lock:
            first = selected not in self._counts
            if first and len(self._counts) >= MAX_HELD_SCOPES:
                message = "extension source scope limit reached"
                raise ValueError(message)
            self._counts[selected] += 1
        with ExitStack() as cleanup:
            cleanup.callback(self._release_scope, selected)
            if first:
                self._changed()
            yield

    def _release_scope(self, scope: ExtensionScope) -> None:
        with self._lock:
            self._counts[scope] -= 1
            last = self._counts[scope] == 0
            if last:
                self._counts.pop(scope)
        if last:
            self._changed()


@dataclass(frozen=True)
class SessionSourceScopes(ExtensionSourceScopes):
    """Add every running actor from committed core read models."""

    sessions: SessionDataAggregateRead
    explicit: ExtensionSourceScopes

    def source_scopes(self) -> tuple[ExtensionScope, ...]:
        """Read complete actor identities without inferring repository IDs from path labels.

        Returns:
            Active core actors and the explicit non-session scope set.

        """
        scopes = set(self.explicit.source_scopes())
        for session in self.sessions.running():
            for actor in session.actors:
                if actor.state is LifecycleState.RUNNING:
                    scopes.add(SessionScope(
                        session_id=actor.session_id, actor_id=actor.actor_id, harness=session.session.harness,
                    ))
        return tuple(sorted(scopes, key=lambda scope: scope.model_dump_json()))

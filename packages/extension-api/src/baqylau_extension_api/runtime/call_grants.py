# Copyright (c) 2026 Zhambyl Yermagambet
"""Track host-issued call authority separately from feature wire requests."""

from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock
from time import monotonic
from uuid import uuid4

from pydantic import TypeAdapter

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.environment import ExtensionEnvironment
from baqylau_extension_api.models.scopes import ExtensionScope
from baqylau_extension_api.runtime.grant_models import MAX_CALL_DEPTH, HostCallGrant
from baqylau_extension_api.runtime.host_context import current_host_call_id, host_call_scope
from baqylau_extension_api.runtime.models import RequestTimeout

MAX_ACTIVE_HOST_CALLS = 1024


class HostCallLedger:
    """Keep active grants in host memory; workers receive only correlation IDs."""

    def __init__(self) -> None:
        """Start with no authority to make a nested peer query."""
        self._lock = Lock()
        self._calls: dict[str, HostCallGrant] = {}

    @contextmanager
    def root(
        self, environment: ExtensionEnvironment, scope: ExtensionScope, timeout: float,
    ) -> Iterator[HostCallGrant]:
        """Authorize one host-selected root call for a bounded period.

        Yields:
            A grant which becomes invalid when this context exits.

        """
        seconds = TypeAdapter(RequestTimeout).validate_python(timeout, strict=True)
        grant = HostCallGrant(
            call_id=uuid4().hex, environment=environment, scope=scope, expires_at=monotonic() + seconds,
            route=(environment.extension_info.extension_id,),
        )
        with self._hold(grant):
            yield grant

    @contextmanager
    def forward(self, parent: HostCallGrant, target: ExtensionEnvironment) -> Iterator[HostCallGrant]:
        """Keep the original scope and deadline while adding the selected peer.

        Yields:
            The child grant with a host-owned route and parent references.

        Raises:
            ExtensionContractError: If the call repeats a peer or exceeds the depth limit.

        """
        with self._lock:
            if self._calls.get(parent.call_id) != parent:
                message = "peer query parent is not an active host grant"
                raise ExtensionContractError(message)
            self._require_active(parent)
        owner = target.extension_info.extension_id
        if owner in parent.route or len(parent.route) >= MAX_CALL_DEPTH:
            message = "peer service call cycle or depth limit"
            raise ExtensionContractError(message)
        grant = HostCallGrant(
            call_id=uuid4().hex, environment=target, scope=parent.scope, expires_at=parent.expires_at,
            route=(*parent.route, owner), parents=(*parent.parents, parent.call_id),
        )
        with self._hold(grant):
            yield grant

    def require_call(self, environment: ExtensionEnvironment, scope: ExtensionScope) -> HostCallGrant:
        """Check the authenticated worker identity, scope, deadline, and active parents.

        Returns:
            The host record, not data supplied by an extension.

        Raises:
            ExtensionContractError: If no live matching grant exists.

        """
        call_id = current_host_call_id()
        with self._lock:
            grant = None if call_id is None else self._calls.get(call_id)
            if grant is None or grant.environment != environment or grant.scope != scope:
                message = "peer query has no matching host call authority"
                raise ExtensionContractError(message)
            self._require_active(grant)
            return grant

    def revoke_runtime(self, environment: ExtensionEnvironment) -> None:
        """Remove one stopped runtime and make its child calls unusable."""
        with self._lock:
            expired = tuple(
                key for key, grant in self._calls.items() if grant.environment == environment
            )
            for key in expired:
                self._calls.pop(key)

    @contextmanager
    def _hold(self, grant: HostCallGrant) -> Iterator[None]:
        with self._lock:
            self._require_active(grant)
            if len(self._calls) >= MAX_ACTIVE_HOST_CALLS:
                message = "host peer call limit reached"
                raise ExtensionContractError(message)
            self._calls[grant.call_id] = grant
        try:
            with host_call_scope(grant.call_id, expires_at=grant.expires_at):
                yield
        finally:
            with self._lock:
                self._calls.pop(grant.call_id, None)

    def _require_active(self, grant: HostCallGrant) -> None:
        missing_parent = any(parent not in self._calls for parent in grant.parents)
        if grant.expires_at <= monotonic() or missing_parent:
            message = "peer query host call expired or its parent was released"
            raise ExtensionContractError(message)

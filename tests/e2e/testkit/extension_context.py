# Copyright (c) 2026 Zhambyl Yermagambet
"""Hold what one live extension scenario uses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sdk.client import BaqylauClient
from tests.e2e.testkit.extension_packages import ExtensionPackages
from tests.e2e.testkit.policy import WaitPolicy
from tests.e2e.testkit.references import Sessions
from tests.extension_host.wheel_fixture import build_wheels

if TYPE_CHECKING:
    from pathlib import Path

    from baqylau_extension_testkit.client import HostClient


def sdk_wheel(directory: Path) -> Path:
    """Build the SDK wheel that each package environment installs.

    Returns:
        The wheel.

    """
    return next(build_wheels(directory).glob("baqylau_extension_api-*.whl"))


@dataclass(frozen=True)
class ExtensionContext:
    """Hold the packages, the client, the named sessions, and the waits of one scenario."""

    packages: ExtensionPackages
    client: BaqylauClient
    sessions: Sessions
    wait_policy: WaitPolicy

    @property
    def host(self) -> HostClient:
        """A client for the extension routes."""
        return self.packages.client

    def session_scope(self, session_name: str) -> dict[str, str]:
        """Name the lead actor's session scope of one session.

        Returns:
            The scope.

        """
        snapshot = self.client.sessions.snapshot(self.sessions.get(session_name))
        session = snapshot.session_data.session
        return {
            "kind": "session", "session_id": snapshot.session_id, "actor_id": session.lead_actor_id,
            "harness": str(session.harness),
        }

    def has_entry(self, session_name: str, entry_type: str, owner: str) -> bool | None:
        """Tell whether the session feed has one entry of a package.

        Returns:
            True when it has one, and None to keep waiting.

        """
        entries = self.client.sessions.snapshot(self.sessions.get(session_name)).entries
        kinds = {_kind(entry.body) for entry in entries}
        return True if (entry_type, owner) in kinds else None


def _kind(body: object) -> tuple[object, object]:
    return getattr(body, "entry_type", None), getattr(body, "owner", None)

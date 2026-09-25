# Copyright (c) 2026 Zhambyl Yermagambet
"""A core pane shows the session views that name it, and reports a failing view by title (P07-T02)."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, cast

from baqylau_extension_api.contracts.plugin import ExtensionCapabilities
from baqylau_extension_api.manifest.views import TerminalView as TerminalContribution
from baqylau_extension_api.models.scopes import SessionScope
from baqylau_extension_api.terminal import blocks, models as terminal_models

from extensions import presentation_snapshots, terminal_sections
from tests.extension_api import service_samples
from tests.extension_host import registry_fixture

if TYPE_CHECKING:
    from baqylau_extension_api.contracts.plugin import ExtensionPlugin

    from extensions.registry_package import RegistryPackage

OWNER = service_samples.ALPHA
SCOPE = SessionScope(session_id="session-one", actor_id="lead", harness="claude")
VIEWPORT = terminal_models.TerminalViewport(columns=60, rows=24)


class Presenter:
    """Present a status for one view, and fail for the broken view."""

    def present(self, terminal_request: terminal_models.TerminalViewRequest) -> terminal_models.TerminalView:
        """Answer the fixture view.

        Returns:
            One status block.

        Raises:
            RuntimeError: For the broken view.

        """
        if terminal_request.binding.view_id.endswith("broken"):
            message = "the fixture view fails"
            raise RuntimeError(message)
        status = blocks.StatusBlock(block_id="state", label="Healthy", tone="success")
        return terminal_models.TerminalView(binding=terminal_request.binding, title="Deploy", blocks=(status,))


@dataclass(frozen=True)
class Plugin:
    """Carry the fixture presenter as the package's terminal capability."""

    capabilities: ExtensionCapabilities


@dataclass
class Snapshots:
    """Record which owners' snapshots are read; answer the default generation at the history start."""

    owners: list[str] = field(default_factory=list)

    def active_generation(self, owner: str) -> str:
        """Name the default generation.

        Returns:
            The generation.

        """
        self.owners.append(owner)
        return "default"

    def committed_cursor(self, owner: str, scope: object, history_revision: str, generation: str) -> int:
        """Start at the history start of the default history.

        Returns:
            Zero.

        """
        assert (scope, history_revision, generation) == (SCOPE, "default", "default")
        self.owners.append(owner)
        return 0


def view(view_id: str, pane: str) -> TerminalContribution:
    """Declare one session view in a pane.

    Returns:
        The declaration.

    """
    owned = f"{OWNER}.{view_id}"
    return TerminalContribution(view_id=owned, title=view_id.title(), scopes=("session",), pane=pane)


def package() -> RegistryPackage:
    """Declare two mirror views and one scoreboard view with the fixture presenter.

    Returns:
        The active package.

    """
    base = registry_fixture.peer(OWNER)
    contributions = base.manifest.contributions.model_copy(update={"terminal": (
        view("deploy", "mirror"), view("broken", "mirror"), view("summary", "scoreboard"),
    )})
    manifest = base.manifest.model_copy(update={"contributions": contributions})
    assert base.plugin is not None
    capabilities = ExtensionCapabilities(lifecycle=base.plugin.capabilities.lifecycle, terminal=Presenter())
    # Only the capabilities are read, so the fixture plugin needs no lifecycle identity.
    return replace(base, manifest=manifest, plugin=cast("ExtensionPlugin", Plugin(capabilities)))


def test_mirror_shows_views_and_failures() -> None:
    """Only views that name the mirror are presented; a failing presenter is reported by title."""
    reads = Snapshots()

    snapshots = presentation_snapshots.PresentationSnapshots(reads, reads, registry_fixture.query_authority())

    found = terminal_sections.pane_sections((package(),), snapshots, "mirror", SCOPE, VIEWPORT)

    assert [section.title for section in found.views] == ["Deploy"]
    assert found.unavailable == ("Broken",)
    assert set(reads.owners) == {OWNER}

"""Which immutable content views the mirror has expanded.

Owns the toggle and its audit row. The route used to call the storage module
directly and write the audit line itself — the only route in the tree that was
its own service — and the pane renderer used to open the database inside its
frame loop.
"""

from __future__ import annotations

import time

from diagnostics.models import StateFileRecord
from repository.contract.diagnostics import DiagnosticWriteRepository
from repository.contract.terminal import ContentViewRepository


class ContentViewService:
    def __init__(
        self,
        views: ContentViewRepository,
        audit: DiagnosticWriteRepository,
        clock=time.time,
    ) -> None:
        self.views = views
        self.audit = audit
        self.clock = clock

    def opened(self) -> frozenset[str]:
        return self.views.opened()

    def toggle(self, content_reference: str) -> bool:
        opened = self.views.toggle(content_reference, self.clock())
        self.audit.record_state_file(
            StateFileRecord(
                session_id="",
                path=content_reference,
                action="terminal-view",
                content="opened" if opened else "closed",
                script="dashboard",
                process_id=0,
                timestamp=self.clock(),
            )
        )
        return opened

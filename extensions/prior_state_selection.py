# Copyright (c) 2026 Zhambyl Yermagambet
"""Give prior canonical facts only to a canonical transformer that declares that it reads them.

Reading, checking, and sending up to 1,000 earlier facts for each input is the
largest cost of a canonical transformer (see `docs/extensions/performance.md`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from baqylau_extension_api.manifest.data import ProcessingSelection
from baqylau_extension_api.models.canonical import CoreStateSnapshot

if TYPE_CHECKING:
    from collections.abc import Iterable

    from baqylau_extension_api.manifest.package import ExtensionManifest


def reads_prior_state(manifest: ExtensionManifest) -> bool:
    """Tell if the package's canonical transformer declares that it reads prior state.

    Returns:
        True when it does.

    """
    return any(
        isinstance(selection, ProcessingSelection) and selection.prior_state
        for selection in manifest.contributions.processing
    )


def any_reads_prior_state(manifests: Iterable[ExtensionManifest]) -> bool:
    """Tell if any active package reads prior state, so that the host must capture it.

    Returns:
        True when one does.

    """
    return any(reads_prior_state(manifest) for manifest in manifests)


def prior_for(manifest: ExtensionManifest, captured: CoreStateSnapshot) -> CoreStateSnapshot:
    """Give the captured prior state, or an empty snapshot that makes no completeness claim.

    Returns:
        The snapshot for this package.

    """
    if reads_prior_state(manifest):
        return captured
    return CoreStateSnapshot(after_cursor=captured.after_cursor)

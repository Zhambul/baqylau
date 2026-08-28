"""Run one terminal action without loss of the user's draft."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from domain.ids import WindowId
from harness.contract import ComposerDriver, HarnessComposer

Result = TypeVar("Result")


class ComposerRestoreError(Exception):
    """The terminal action ran, but its saved draft could not be restored."""


def with_preserved_draft(
    harness_composer: HarnessComposer,
    composer_driver: ComposerDriver,
    window_id: WindowId,
    action: Callable[[], Result],
) -> Result:
    """Clear the composer, run `action`, and restore the exact visible draft."""
    state = harness_composer.read(composer_driver, window_id)
    if state is None:
        raise ComposerRestoreError("the terminal composer is not readable")
    draft = state.typed_text or ""
    harness_composer.clear(composer_driver, window_id)
    try:
        result = action()
    except BaseException as action_error:
        try:
            harness_composer.insert(composer_driver, window_id, draft)
        except Exception as restore_error:
            raise ComposerRestoreError(
                f"the terminal action failed and the draft was not restored: {restore_error}"
            ) from action_error
        raise
    try:
        harness_composer.insert(composer_driver, window_id, draft)
    except Exception as restore_error:
        raise ComposerRestoreError(
            f"the terminal draft was not restored: {restore_error}"
        ) from restore_error
    return result

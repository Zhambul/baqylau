# Copyright (c) 2026 Zhambyl Yermagambet
"""A terminal view is presented once for each data, settings, runtime, size, and focus (P07-T03)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from extensions.presentation_cache import PresentationCache
from tests.extension_api import terminal_samples

if TYPE_CHECKING:
    from baqylau_extension_api.terminal.models import TerminalView, TerminalViewRequest


WIDE = 120
ROW_COUNTS = (10, 11, 12)


class CountingPresenter:
    """Count the worker calls that the cache lets through."""

    def __init__(self) -> None:
        """Start with no calls."""
        self.calls = 0

    def present(self, request: TerminalViewRequest) -> TerminalView:
        """Give a view bound to the request.

        Returns:
            The sample view with the request's binding.

        """
        self.calls += 1
        return terminal_samples.action_response().model_copy(update={"binding": request.binding})


def _at_cursor(request: TerminalViewRequest, cursor: int) -> TerminalViewRequest:
    snapshot = request.binding.snapshot.model_copy(update={"commit_cursor": cursor})
    binding = request.binding.model_copy(update={"snapshot": snapshot})
    return request.model_copy(update={"binding": binding})


def _sized(request: TerminalViewRequest, **size: int) -> TerminalViewRequest:
    viewport = request.viewport.model_copy(update=size)
    return request.model_copy(update={"viewport": viewport})


def _present(cache: PresentationCache, presenter: CountingPresenter, request: TerminalViewRequest) -> TerminalView:
    return cache.presented(request, lambda: presenter.present(request))


def test_same_request_uses_the_kept_view() -> None:
    """A repaint with no new data does not call the presenter again."""
    cache = PresentationCache()
    presenter = CountingPresenter()
    request = terminal_samples.view_request()

    first = _present(cache, presenter, request)
    second = _present(cache, presenter, request)

    assert second is first
    assert presenter.calls == 1


def test_new_data_or_size_presents_again() -> None:
    """A new committed cursor or a new pane size is a new request."""
    cache = PresentationCache()
    presenter = CountingPresenter()
    request = terminal_samples.view_request()

    requests = (request, _at_cursor(request, 2), _sized(request, columns=WIDE))
    for selected in requests:
        _present(cache, presenter, selected)

    assert presenter.calls == len(requests)


def test_oldest_view_leaves_first() -> None:
    """The cache keeps at most its limit; a recent read keeps a view."""
    cache = PresentationCache(limit=2)
    presenter = CountingPresenter()
    sizes = [_sized(terminal_samples.view_request(), rows=rows) for rows in ROW_COUNTS]

    for index in (0, 1, 0, 2):
        _present(cache, presenter, sizes[index])
    kept = presenter.calls
    _present(cache, presenter, sizes[0])
    assert presenter.calls == kept
    _present(cache, presenter, sizes[1])
    assert presenter.calls == kept + 1

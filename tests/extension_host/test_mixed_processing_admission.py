# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep empty reads and application stop separate from timed progress."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from core.work_queue import WorkKind
from engine import mixed_processing
from tests.extension_host import mixed_processing_fixture as fixture


def test_empty_queue_clears_expired_interval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An expired interval cannot turn an empty queue into repeated idle work."""
    case = fixture.installed(tmp_path, monkeypatch)
    case.run()
    expired = Mock(return_value=True)
    monkeypatch.setattr(mixed_processing.ProcessingSlice, "expired", expired)
    case.run()
    assert not case.pending()
    expired.assert_not_called()
    case.engine.queue.set_deadline.assert_called_with(WorkKind.RAW, None, mixed_processing.CONTINUATION_KEY)


def test_stop_precedes_expired_first_original(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Timed progress must not override a request to stop before the first original."""
    case = fixture.installed(tmp_path, monkeypatch)
    before = case.pending()
    core = case.engine.interpreter.translation
    expired = Mock(return_value=True)
    with case.source.runtime.capture_batch() as batch:
        assert batch is not None
        assert batch.interpret_pending(core, Mock(return_value=True), yield_requested=expired) == 0
    assert case.pending() == before
    expired.assert_not_called()
    core.accept_interpretation.assert_not_called()

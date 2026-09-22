# Copyright (c) 2026 Zhambyl Yermagambet
"""Send source work notices only after a committed read supplies original input."""

from pathlib import Path

import pytest

from core.work_queue import WorkKind, WorkQueue
from tests.extension_host import source_read_fixture as fixtures


@pytest.mark.parametrize("emit", [False, True])
def test_source_commit_sends_only_raw_notice(tmp_path: Path, *, emit: bool) -> None:
    """Empty checkpoints do not start interpretation or canonical reactions."""
    case = fixtures.installed(tmp_path)
    queue = WorkQueue()
    case.store.database.work_queue = queue
    queue.put(WorkKind.EXTENSIONS)
    case.store.record_source_read(fixtures.proposal(case, emit=emit))
    expected = {WorkKind.EXTENSIONS, WorkKind.RAW} if emit else {WorkKind.EXTENSIONS}
    assert queue.take() == expected


def test_rejected_source_sends_no_notice(tmp_path: Path) -> None:
    """A failed source write does not schedule interpretation work."""
    case = fixtures.installed(tmp_path)
    stale = fixtures.proposal(case, "stale", "position-2")
    case.store.record_source_read(fixtures.proposal(case))
    queue = WorkQueue()
    case.store.database.work_queue = queue
    queue.put(WorkKind.EXTENSIONS)
    with pytest.raises(ValueError, match="checkpoint is stale"):
        case.store.record_source_read(stale)
    assert queue.take() == {WorkKind.EXTENSIONS}


def test_exact_retry_sends_no_notice(tmp_path: Path) -> None:
    """A repeated call does not wake the engine or add another pending row."""
    case = fixtures.installed(tmp_path)
    request = fixtures.proposal(case)
    case.store.record_source_read(request)
    queue = WorkQueue()
    case.store.database.work_queue = queue
    queue.put(WorkKind.EXTENSIONS)
    case.store.record_source_read(request)
    assert queue.take() == {WorkKind.EXTENSIONS}

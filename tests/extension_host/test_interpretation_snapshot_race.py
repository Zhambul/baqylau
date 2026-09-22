# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep capture in one read transaction while another connection commits."""

from pathlib import Path

import pytest

from repository.impl.sqlite import interpretation_codec
from tests.extension_host import (
    interpretation_snapshot_fixture as fixture,
    interpretation_snapshot_writer as writers,
)


def test_capture_keeps_one_sql_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A concurrent commit changes the next head, not the open capture."""
    store = fixture.repository(tmp_path)
    fixture.seed(store, (fixture.fact("first"),))
    request = fixture.request(store)
    writer = writers.SnapshotWriter(fixture.repository(tmp_path), interpretation_codec.stored_fact)
    fixture.request(writer.store)
    monkeypatch.setattr(interpretation_codec, "stored_fact", writer)
    captured = store.capture_prior_state(request)
    assert writer.written
    assert captured.complete and captured.after_cursor == 1
    assert tuple(prior.fact.event_id for prior in captured.facts) == ("first",)
    assert fixture.request(writer.store).expected_canonical_cursor == request.expected_canonical_cursor + 1
    with pytest.raises(ValueError, match="boundary is stale"):
        store.capture_prior_state(request)

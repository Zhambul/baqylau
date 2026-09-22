# Copyright (c) 2026 Zhambyl Yermagambet
"""Bound encoded snapshots before a large stored body can consume the whole page."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from extensions.models.interpretation_snapshot import MAX_PRIOR_BYTES, MIN_PRIOR_BYTES
from repository.impl.sqlite import interpretation_codec
from tests.extension_host import interpretation_snapshot_fixture as fixture

BYTE_FIELD = "max_bytes"
SMALL_BUDGET = 2048
LARGE_TEXT = 4096
ESCAPED_TEXT = (
    ' \n'
    r' "é\n📘"'
    ' \r\n'
)


@pytest.mark.parametrize("encoded", ['"plain"', ESCAPED_TEXT, r'"quote: \" slash: \\"'])
def test_complete_wire_size_sets_the_byte_limit(tmp_path: Path, encoded: str) -> None:
    """The byte count includes JSON escaping, UTF-8 bytes, metadata, and arrays."""
    store = fixture.repository(tmp_path)
    fixture.seed(store, (
        fixture.fact("first", encoded=encoded), fixture.fact("second", encoded=encoded),
    ))
    request = fixture.request(store)
    complete = store.capture_prior_state(request)
    required = fixture.wire_size(complete.model_copy(update={"complete": False}))
    assert store.capture_prior_state(request.model_copy(update={BYTE_FIELD: required})) == complete
    short = store.capture_prior_state(request.model_copy(update={BYTE_FIELD: required - 1}))
    assert complete.complete
    assert not short.complete and len(short.facts) == 1
    assert fixture.wire_size(complete) <= required
    assert fixture.wire_size(short) <= required - 1


@pytest.mark.parametrize("budget", [MIN_PRIOR_BYTES, MAX_PRIOR_BYTES])
def test_large_first_body_is_not_decoded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, budget: int) -> None:
    """Metadata inspection rejects a body which cannot fit before decoding it."""
    store = fixture.repository(tmp_path)
    encoded = '"{text}"'.format(text="x" * (MAX_PRIOR_BYTES - 2))
    fixture.seed(store, (fixture.fact("large", encoded=encoded),))
    request = fixture.request(store).model_copy(update={BYTE_FIELD: budget})
    decoder = Mock(side_effect=AssertionError("The oversized body must not be decoded"))
    monkeypatch.setattr(interpretation_codec, "stored_fact", decoder)
    captured = store.capture_prior_state(request)
    assert not captured.complete and not captured.facts
    assert fixture.wire_size(captured) <= budget
    decoder.assert_not_called()


def test_large_middle_body_does_not_skip_a_cursor(tmp_path: Path) -> None:
    """Capture stops at the first omitted fact, even if a later fact would fit."""
    store = fixture.repository(tmp_path)
    encoded = '"{text}"'.format(text="x" * LARGE_TEXT)
    fixture.seed(store, (
        fixture.fact("first"), fixture.fact("large", encoded=encoded), fixture.fact("last"),
    ))
    request = fixture.request(store).model_copy(update={BYTE_FIELD: SMALL_BUDGET})
    captured = store.capture_prior_state(request)
    assert not captured.complete and captured.after_cursor == request.expected_canonical_cursor
    assert tuple(prior.fact.event_id for prior in captured.facts) == ("first",)
    assert fixture.wire_size(captured) <= SMALL_BUDGET

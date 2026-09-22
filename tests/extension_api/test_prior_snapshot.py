# Copyright (c) 2026 Zhambyl Yermagambet
"""Make prior-state completeness explicit without changing older journal meaning."""

import pytest
from baqylau_extension_api.models.canonical import CoreStateSnapshot


def test_missing_coverage_has_no_complete_claim() -> None:
    """An older snapshot cannot prove that an absent fact does not exist."""
    snapshot = CoreStateSnapshot.model_validate_json('{"after_cursor":0,"facts":[]}')
    assert not snapshot.complete
    assert CoreStateSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot


@pytest.mark.parametrize("complete", [False, True])
def test_complete_flag_round_trip(*, complete: bool) -> None:
    """Both explicit coverage values survive the strict wire boundary."""
    snapshot = CoreStateSnapshot(after_cursor=0, complete=complete)
    assert CoreStateSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot


@pytest.mark.parametrize("encoded", ["1", '"true"', "null", "[]"])
def test_complete_flag_requires_boolean(encoded: str) -> None:
    """Numbers, text, null, and arrays are not coverage evidence."""
    with pytest.raises(ValueError, match="complete"):
        CoreStateSnapshot.model_validate_json(f'{{"after_cursor":0,"complete":{encoded}}}')

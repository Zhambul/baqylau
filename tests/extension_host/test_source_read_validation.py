# Copyright (c) 2026 Zhambyl Yermagambet
"""Recheck source identity, schema, and causes at the public repository boundary."""

from pathlib import Path

import pytest

from extensions.models.source_reads import SourceCheckpoint
from tests.extension_host import source_read_fixture as fixtures
from tests.extension_host.source_read_assertions import require_rejected


@pytest.mark.parametrize("encoded", ["{", "{}", '"valid" trailing'])
def test_invalid_original_rejects(tmp_path: Path, encoded: str) -> None:
    """Malformed JSON or a wrong document type cannot save input or progress."""
    case = fixtures.installed(tmp_path)
    request = fixtures.proposal(case)
    positioned = request.proposal.response.observations[0]
    candidate = positioned.observation.model_copy(update={
        "document": positioned.observation.document.model_copy(update={"json_text": encoded}),
    })
    response = request.proposal.response.model_copy(update={
        "observations": (positioned.model_copy(update={"observation": candidate}),),
    })
    require_rejected(case, request.model_copy(update={
        "proposal": request.proposal.model_copy(update={"response": response}),
    }), r"JSON|schema")


def test_missing_cause_rolls_back_journal(tmp_path: Path) -> None:
    """A missing original cause rejects the full call even after the journal insert."""
    case = fixtures.installed(tmp_path)
    request = fixtures.proposal(case)
    positioned = request.proposal.response.observations[0]
    candidate = positioned.observation.model_copy(update={"causes": ("missing-cause",)})
    response = request.proposal.response.model_copy(update={
        "observations": (positioned.model_copy(update={"observation": candidate}),),
    })
    require_rejected(case, request.model_copy(update={
        "proposal": request.proposal.model_copy(update={"response": response}),
    }), "cause is not recorded")


def test_undeclared_empty_source_rejects(tmp_path: Path) -> None:
    """Empty output still requires a declared source type before progress can advance."""
    case = fixtures.installed(tmp_path)
    request = fixtures.proposal(case, emit=False)
    source = request.proposal.request.source.model_copy(update={"source_type": "test.sample.unknown"})
    request = fixtures.with_request(request, request.proposal.request.model_copy(
        update={"source": source},
    ))
    require_rejected(case, request, "source type")


def test_committed_source_type_cannot_change(tmp_path: Path) -> None:
    """A stable source identity cannot silently select a different decoder after progress exists."""
    case = fixtures.installed(tmp_path)
    case.store.record_source_read(fixtures.proposal(case))
    request = fixtures.proposal(case, "read-2", "position-2", emit=False)
    source = request.proposal.request.source.model_copy(update={"source_type": "test.sample.unknown"})
    request = fixtures.with_request(request, request.proposal.request.model_copy(
        update={"source": source},
    ))
    require_rejected(case, request, "committed source type")


@pytest.mark.parametrize(("revision", "source_type", "position"), [
    (0, "type", None), (0, None, "position"),
    (1, None, "position"), (1, "type", None),
])
def test_partial_checkpoint_rejects(
    tmp_path: Path, revision: int, source_type: str | None, position: str | None,
) -> None:
    """Progress is either absent or a complete typed checkpoint."""
    case = fixtures.installed(tmp_path)
    key = fixtures.proposal(case).proposal.checkpoint.key
    with pytest.raises(ValueError, match="source checkpoint"):
        SourceCheckpoint(key=key, revision=revision, source_type=source_type, position=position)

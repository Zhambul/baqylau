# Copyright (c) 2026 Zhambyl Yermagambet
"""Bound complete projection requests, key selections, and write proposals."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.base import MAX_TEXT_LENGTH
from baqylau_extension_api.models.documents import MAX_DOCUMENT_CHARACTERS
from baqylau_extension_api.models.projections import MAX_PROJECTION_ROWS, ProjectionReadSet
from baqylau_extension_api.projection import boundaries, results, selection

from tests.extension_api import projection_samples

LARGE_BATCH_START = 11
LARGE_BATCH_STOP = 19
LARGE_READ_SET_COUNT = 256
FILL_CHARACTER = "x"


def test_projection_bounds_total_output_rows() -> None:
    """Count records and feed rows together, not as separate unlimited outputs."""
    response = projection_samples.result().model_copy(update={"entries": tuple(
        projection_samples.entry(str(index)) for index in range(MAX_PROJECTION_ROWS)
    )})
    with pytest.raises(ExtensionContractError, match="total row limit"):
        results.validate_projection_result(projection_samples.request(), response)


def test_projection_bounds_encoded_result_bytes() -> None:
    """Reject oversized complete output even when each document fits its field."""
    document = projection_samples.entry().document.model_copy(update={
        "json_text": FILL_CHARACTER * MAX_DOCUMENT_CHARACTERS,
    })
    response = projection_samples.result().model_copy(update={"entries": tuple(
        projection_samples.entry(str(index)).model_copy(update={"document": document})
        for index in range(4)
    )})
    with pytest.raises(ExtensionContractError, match="encoded size limit"):
        results.validate_projection_result(projection_samples.request(), response)


def test_projection_bounds_encoded_input_bytes() -> None:
    """Reject an oversized snapshot before any pure callback starts."""
    request = projection_samples.request()
    row = projection_samples.stored_record()
    document = row.document.model_copy(update={"json_text": FILL_CHARACTER * MAX_DOCUMENT_CHARACTERS})
    request = request.model_copy(update={"prior_records": tuple(
        row.model_copy(update={
            "key": row.key.model_copy(update={"key": str(index)}), "document": document,
        })
        for index in range(8)
    )})
    with pytest.raises(ExtensionContractError, match="encoded size limit"):
        boundaries.validate_request_boundary(request)


def test_projection_bounds_selection_bytes() -> None:
    """Apply the same input byte limit before selecting record keys."""
    request = projection_samples.selection_request()
    stored = request.events[0]
    fact = stored.fact
    document = projection_samples.entry().document.model_copy(update={
        "json_text": FILL_CHARACTER * MAX_DOCUMENT_CHARACTERS,
    })
    request = request.model_copy(update={
        "binding": request.binding.model_copy(update={
            "context": request.binding.context.model_copy(update={"input_cursor": LARGE_BATCH_STOP - 1}),
        }),
        "events": tuple(stored.model_copy(update={
            "cursor": index, "fact": fact.model_copy(update={"event_id": str(index), "document": document}),
        }) for index in range(LARGE_BATCH_START, LARGE_BATCH_STOP)),
    })
    with pytest.raises(ExtensionContractError, match="encoded size limit"):
        boundaries.validate_selection_boundary(request)


def test_projection_bounds_read_set_bytes() -> None:
    """Do not return an unbounded repeated path or record key list."""
    request = projection_samples.selection_request()
    base_key = projection_samples.record_key()
    keys = tuple(
        base_key.model_copy(update={"key": _long_key(index)})
        for index in range(LARGE_READ_SET_COUNT)
    )
    response = ProjectionReadSet(binding=request.binding, keys=keys)
    with pytest.raises(ExtensionContractError, match="read set exceeds"):
        selection.validate_read_set(request, response)


def _long_key(index: int) -> str:
    prefix = str(index).zfill(4)
    suffix = FILL_CHARACTER * (MAX_TEXT_LENGTH - len(prefix))
    return f"{prefix}{suffix}"

# Copyright (c) 2026 Zhambyl Yermagambet
"""Check the real observer request, reply, and output count bounds."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.documents import ContentReference
from baqylau_extension_api.models.observations import MAX_OBSERVATIONS
from baqylau_extension_api.models.observer_jobs import ObservationJobRequest
from baqylau_extension_api.observers import requests, results
from baqylau_extension_api.schemas import SchemaSet
from pydantic import ValidationError

from tests.extension_api import observer_samples as fixtures, operation_samples

LARGE_TEXT_LENGTH = 1_000_000
REFERENCE_COUNT = 2200
REFERENCE_LENGTH = 4000
RESPONSE_ROWS = 5
SHA256_LENGTH = 64


def test_observer_request_has_real_byte_bound() -> None:
    """A large but field-valid core trigger cannot bypass the eight MiB limit."""
    request = _large_request()
    assert ObservationJobRequest.model_validate(request) == request
    assert len(request.model_dump_json().encode()) > requests.MAX_OBSERVER_REQUEST_BYTES
    manifest = fixtures.manifest()
    with pytest.raises(ExtensionContractError, match="encoded size limit"):
        requests.validate_observation_request(manifest, SchemaSet(manifest.schemas), request)


def test_observer_response_has_real_byte_bound() -> None:
    """Several valid original documents cannot bypass the four MiB reply limit."""
    response = fixtures.succeeded()
    text = "x" * LARGE_TEXT_LENGTH
    document = operation_samples.query_request(f'"{text}"').arguments
    observations = tuple(response.observations[0].model_copy(update={
        "observation_key": f"result-{index}", "document": document,
    }) for index in range(RESPONSE_ROWS))
    with pytest.raises(ExtensionContractError, match="encoded size limit"):
        results.validate_observation_result(
            response.binding, response.model_copy(update={"observations": observations}),
        )


def test_observer_output_count_is_bounded() -> None:
    """Small documents still have a fixed result-count limit."""
    response = fixtures.succeeded()
    observations = response.observations * (MAX_OBSERVATIONS + 1)
    with pytest.raises(ValidationError):
        results.validate_observation_result(
            response.binding, response.model_copy(update={"observations": observations}),
        )


def test_observer_content_ids_are_unique() -> None:
    """Two references cannot claim one owned output identity in the same reply."""
    response = fixtures.succeeded()
    content = ContentReference(content_id="content", media_type="text/plain", byte_length=0, digest="a" * SHA256_LENGTH)
    with pytest.raises(ExtensionContractError, match="content IDs"):
        results.validate_observation_result(
            response.binding, response.model_copy(update={"content": (content, content)}),
        )


def _large_request() -> ObservationJobRequest:
    request = fixtures.core_request()
    text = "x" * REFERENCE_LENGTH
    fact = request.event.fact.model_copy(update={
        "raw_event_ids": tuple(f"{index}:{text}" for index in range(REFERENCE_COUNT)),
    })
    event = request.event.model_copy(update={"fact": fact})
    return request.model_copy(update={"event": event})

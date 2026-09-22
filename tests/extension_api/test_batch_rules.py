# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject ambiguous input batches and conflicting transform decisions."""

import pytest
from baqylau_extension_api.models.canonical import CoreStateSnapshot
from baqylau_extension_api.models.raw_transforms import RawTransformResult
from baqylau_extension_api.models.scopes import InstallationScope
from baqylau_extension_api.models.transforms import (
    CanonicalTransformRequest,
    Drop,
    Insert,
    Keep,
    RawTransformRequest,
)
from pydantic import ValidationError

from extensions.mapper.core_events import public_candidate
from tests.extension_api import core_samples, samples

RAW_ID = "raw-1"
OUTPUT_KEY = "extra"


def test_raw_batch_rejects_duplicate_input_ids() -> None:
    """Require an unambiguous anchor for each operation."""
    with pytest.raises(ValidationError, match="unique"):
        RawTransformRequest(
            context=samples.processing_context(), inputs=(samples.raw_input(), samples.raw_input()),
        )


def test_raw_batch_rejects_other_scope() -> None:
    """Do not let an input change the scope pinned on its request."""
    source = samples.raw_input().model_copy(update={"scope": InstallationScope()})
    with pytest.raises(ValidationError, match="processing scope"):
        RawTransformRequest(context=samples.processing_context(), inputs=(source,))


def test_canonical_batch_rejects_repeated_ids() -> None:
    """Apply the same identity rule to the full canonical union."""
    fact = public_candidate(core_samples.original_event(core_samples.original_payload(core_samples.PAYLOADS[0])))
    context = samples.processing_context().model_copy(update={"scope": fact.scope})
    with pytest.raises(ValidationError, match="unique"):
        CanonicalTransformRequest(context=context, inputs=(fact, fact), prior_state=CoreStateSnapshot(after_cursor=0))


def test_result_rejects_conflicting_decisions() -> None:
    """Do not let operation ordering choose between keep and drop."""
    with pytest.raises(ValidationError, match="only one"):
        RawTransformResult(operations=(
            Keep(input_id=RAW_ID), Drop(input_id=RAW_ID, reason="suppressed"),
        ))


def test_result_rejects_repeated_addition_key() -> None:
    """Reject the same logical addition on both sides of an input."""
    addition = Insert(
        document=samples.raw_input("extra"), input_id=RAW_ID, output_key=OUTPUT_KEY, position="before",
    )
    with pytest.raises(ValidationError, match="unique per input"):
        RawTransformResult(operations=(addition, addition.model_copy(update={"position": "after"})))


def test_addition_keys_are_relative_to_input() -> None:
    """Allow a transformer to use the same output key for different inputs."""
    first = Insert(
        document=samples.raw_input("extra-1"), input_id=RAW_ID, output_key=OUTPUT_KEY, position="after",
    )
    second = Insert(
        document=samples.raw_input("extra-2"), input_id="raw-2", output_key=OUTPUT_KEY, position="after",
    )
    assert RawTransformResult(operations=(first, second)).operations == (first, second)

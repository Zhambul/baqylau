# Copyright (c) 2026 Zhambyl Yermagambet
"""Preserve originals across raw replacement, suppression, and stable additions."""

from pathlib import Path

from baqylau_extension_api.identities import DerivedIdentity, derived_input_id
from baqylau_extension_api.models import content, events, raw_transforms, transforms

from tests.extension_host import (
    interpretation_fixture as fixtures,
    interpretation_raw as raw,
    interpretation_results as evidence,
    interpretation_transforms as canonical,
)

CHANGED_INPUT = content.encode_content(b' "changed input"\n', "application/json")


def test_raw_replacement_preserves_original(tmp_path: Path) -> None:
    """New translation content cannot replace stored original bytes."""
    case = fixtures.installed(tmp_path, canonical.raw_manifest())
    request = fixtures.proposal(case)
    before = case.original.store.find_observation(request.proposal.binding.raw_event_id)
    source = raw.request(request).inputs[0]
    request = raw.apply(request, raw_transforms.RawTransformResult(
        operations=(transforms.Replace(input_id=source.input_id, document=source.model_copy(
            update={"content": CHANGED_INPUT.reference},
        )),), content_snapshot=content.ContentBundle(blobs=(CHANGED_INPUT,)),
    ))
    fact = case.store.record_interpretation(request).accepted[0]
    assert fact.fact == request.proposal.facts[0]
    assert case.original.store.find_observation(request.proposal.binding.raw_event_id) == before
    assert isinstance(fact.fact, events.ExtensionFact)
    assert fact.fact.document.json_text == ' "changed input"\n'


def test_raw_drop_does_not_advance_decoder(tmp_path: Path) -> None:
    """Dropping the entire raw input records suppression but never claims a decoder call."""
    case = fixtures.installed(tmp_path, canonical.raw_manifest())
    request = fixtures.proposal(case)
    request = raw.apply(request, raw_transforms.RawTransformResult(operations=(
        transforms.Drop(input_id=raw.request(request).inputs[0].input_id, reason="Skip input"),
    )))
    assert not case.store.record_interpretation(request).accepted
    assert not case.original.store.pending_observations(10)
    assert case.store.translator_state(fixtures.state_key(case)).revision == 0
    assert case.store.find_interpretation("default", request.proposal.binding.raw_event_id) == request


def test_added_raw_input_keeps_each_proposal(tmp_path: Path) -> None:
    """Two derived inputs can propose one logical fact while retaining both decisions."""
    case = fixtures.installed(tmp_path, canonical.raw_manifest())
    request = fixtures.proposal(case)
    source = raw.request(request).inputs[0]
    identity = derived_input_id(DerivedIdentity(
        extension_id=source.owner, input_id=source.input_id, output_key="extra",
    ))
    request = raw.apply(request, raw_transforms.RawTransformResult(operations=(transforms.Insert(
        input_id=source.input_id, output_key="extra", position="after",
        document=source.model_copy(update={"input_id": identity}),
    ),)))
    assert len(case.store.record_interpretation(request).accepted) == 1
    decided = tuple(decision.input_id for decision in evidence.reply(request).decisions)
    assert decided == (source.input_id, identity)
    assert case.store.find_interpretation("default", request.proposal.binding.raw_event_id) == request

# Copyright (c) 2026 Zhambyl Yermagambet
"""Codex plan and patch translation tests."""

from domain.event_work import PlanProposed
from harness.impl.codex.canonical.translator import CodexCanonicalTranslator
from tests.harness_names import CODEX_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import payloads, raw_event
from tests.plugin_tests.support_values import text_of


def test_codex_plan_has_a_canonical_fact() -> None:
    """Verify codex plan has a canonical fact."""
    plan = CodexCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.ITEM_COMPLETED,
                    fixture.ITEM_FIELD: {
                        fixture.TYPE_FIELD: "Plan",
                        fixture.ID_FIELD: fixture.PLAN_ONE,
                        fixture.TEXT_FIELD: "1. Change it",
                    },
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="plan",
        ),
    )

    proposed = payloads(plan, PlanProposed)[0].payload
    assert proposed.attention_id == fixture.PLAN_ONE
    assert text_of(proposed.plan) == "1. Change it"


def test_codex_plan_response_wrapper_is_covered() -> None:
    """Verify codex plan response wrapper is covered by the structured plan item."""
    wrapper = CodexCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.MESSAGE_FIELD,
                    fixture.ROLE_FIELD: fixture.ASSISTANT,
                    fixture.CONTENT_FIELD: "<proposed_plan>1. Change it</proposed_plan>",
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="plan-wrapper",
        ),
    )

    assert wrapper.decision == fixture.IGNORED_NONSEMANTIC
    assert wrapper.canonical_events == ()


def test_codex_preliminary_patch_marker() -> None:
    """Verify codex preliminary patch marker is nonsemantic."""
    translation = CodexCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: "apply_patch",
                    fixture.CALL_ID_FIELD: "patch-one",
                    fixture.INPUT_FIELD: "*** Begin Patch",
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="patch-call",
        ),
    )

    assert translation.decision == fixture.IGNORED_NONSEMANTIC
    assert translation.canonical_events == ()


def test_codex_exec_wrapped_apply_patch_does_not() -> None:
    """Verify codex exec wrapped apply patch does not render an empty tool block."""
    translated = CodexCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: "patch-one",
                    fixture.INPUT_FIELD: "text(await tools.apply_patch(patch));",
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="wrapped-patch-call",
        ),
    )

    assert translated.decision == fixture.IGNORED_NONSEMANTIC
    assert translated.canonical_events == ()


def test_codex_batched_apply_patch_calls() -> None:
    """Verify codex batched apply patch calls are known request plumbing."""
    translated = CodexCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: "patch-batch",
                    fixture.INPUT_FIELD: (
                        "const p1 = '*** Begin Patch';"
                        "await tools.apply_patch(p1);"
                        "const p2 = '*** Begin Patch';"
                        "await tools.apply_patch(p2);"
                    ),
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="patch-batch-call",
        ),
    )

    assert translated.canonical_events == ()
    assert translated.decision == fixture.IGNORED_NONSEMANTIC


def test_codex_apply_patch_wrapper_output() -> None:
    """Verify codex apply patch wrapper output is nonsemantic."""
    translation = CodexCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_OUTPUT_ID,
                    fixture.CALL_ID_FIELD: "patch-one",
                    fixture.OUTPUT_FIELD: [
                        {
                            fixture.TYPE_FIELD: fixture.INPUT_TEXT_ID,
                            fixture.TEXT_FIELD: fixture.SCRIPT_COMPLETED_OUTPUT_TEXT,
                        },
                        {fixture.TYPE_FIELD: fixture.INPUT_TEXT_ID, fixture.TEXT_FIELD: "{}"},
                    ],
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="wrapped-patch-output",
        ),
    )

    assert translation.decision == fixture.IGNORED_NONSEMANTIC
    assert translation.canonical_events == ()

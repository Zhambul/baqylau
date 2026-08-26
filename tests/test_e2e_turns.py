"""Focused checks for live-E2E turn assertions."""

import pytest

from tests.e2e.testkit.turns import matches_final_answer


@pytest.mark.parametrize(
    ("observed", "matches"),
    (
        ("attachment-marker-731", True),
        ("attachment-marker-731.", True),
        ("```\nattachment-marker-731\n```", True),
        ("```text\nattachment-marker-731\n```", True),
        ("Here it is: attachment-marker-731", False),
        ("```\nwrong-marker\n```", False),
        ("```python\nattachment-marker-731\n```", False),
    ),
)
def test_final_answer_matcher_tolerates_only_harmless_wrappers(
    observed: str,
    matches: bool,
) -> None:
    assert matches_final_answer(observed, "attachment-marker-731") is matches

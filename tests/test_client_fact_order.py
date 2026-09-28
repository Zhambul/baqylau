# Copyright (c) 2026 Zhambyl Yermagambet
"""The Kitty pane shows a late row at the fact that it belongs to."""

from __future__ import annotations

from tests import client_test_models, test_client_loading

LATE_FACT = 2


def _message(entry_id: str) -> client_test_models.JsonValue:
    body: dict[str, client_test_models.JsonValue] = {
        "role": "assistant", "phase": "end_turn", "content": {"text": f"message {entry_id}"},
    }
    return client_test_models.lead_pane_entry(entry_id, "message", body)


def test_a_late_row_shows_at_its_fact() -> None:
    """A stream row that a projector wrote after later facts goes to its fact, not to the end."""
    model = test_client_loading.load_shared("_model").SessionModel()
    page = [_message("1"), _message("2"), _message("3")]
    model.apply_page({"items": page})
    late = _message("4")
    assert isinstance(late, dict)
    late["commit_cursor"] = LATE_FACT

    model.apply_frame({"entries": [late]})

    assert [record.entry_id for record in model.feed()] == ["1", "2", "4", "3"]

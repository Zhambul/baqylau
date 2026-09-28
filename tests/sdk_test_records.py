# Copyright (c) 2026 Zhambyl Yermagambet
"""Build SDK records for focused state tests."""

from api.sessiondata.models.entry import EntryResponse


def assignment_started_entry(assignment_id: str, cursor: int) -> EntryResponse:
    """Build one pending assignment entry.

    Returns:
        The assignment entry.

    """
    return EntryResponse.model_validate(
        {
            "entry_id": f"assignment-started-{cursor}",
            "type": "assignment_started",
            "cursor": cursor,
            "commit_cursor": cursor,
            "actor_id": "lead-one",
            "parent_actor_id": None,
            "turn_id": "turn-one",
            "occurred_at": float(cursor),
            "summary": None,
            "body": {
                "assignment_id": assignment_id,
                "assigned_actor_name": assignment_id,
                "prompt": {"text": "same work", "media_type": "text/plain"},
            },
        },
    )


def plan_entry(cursor: int, entry_type: str, body: dict[str, object]) -> EntryResponse:
    """Build one plan entry.

    Returns:
        The plan entry.

    """
    return EntryResponse.model_validate(
        {
            "entry_id": f"plan-{cursor}",
            "type": entry_type,
            "cursor": cursor,
            "commit_cursor": cursor,
            "actor_id": "lead-one",
            "parent_actor_id": None,
            "turn_id": "turn-one",
            "occurred_at": float(cursor),
            "summary": None,
            "body": {"attention_id": "plan-one", **body},
        },
    )

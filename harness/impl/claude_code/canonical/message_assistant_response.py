# Copyright (c) 2026 Zhambyl Yermagambet
"""Read Claude Code assistant response data."""

from harness.impl.claude_code import model_names
from harness.impl.claude_code.canonical import message_models, records, transcript
from harness.impl.claude_code.canonical.support import SYNTHETIC_MODEL_ID, model_reference


def assistant_response(record: transcript.AssistantTranscriptRecord) -> message_models.AssistantResponse:
    """Read content, model, and turn-ending fields from an assistant record.

    Returns:
        The response data with the index of its last nonempty text block.

    """
    message = record.message
    blocks = []
    if message is not None and isinstance(message.content, list):
        blocks = message.content
    model_id = message.model if message else None
    reference = (
        model_reference(model_names.record_model(model_id)) if model_id and model_id != SYNTHETIC_MODEL_ID else None
    )
    return message_models.AssistantResponse(
        message,
        blocks,
        message is not None and message.stop_reason == "end_turn",
        _last_text_index(blocks),
        message_models.AssistantModel(model_id, reference),
    )


def _last_text_index(blocks: list[records.MessageContentBlock]) -> int:
    return max(
        (
            index
            for index, block in enumerate(blocks)
            if isinstance(block, records.TextBlock) and (block.text or "").strip()
        ),
        default=-1,
    )

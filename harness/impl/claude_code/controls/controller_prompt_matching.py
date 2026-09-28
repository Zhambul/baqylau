# Copyright (c) 2026 Zhambyl Yermagambet
"""Match Claude Code prompts to native transcript records."""

import re

from pydantic import ValidationError

from harness.impl.claude_code.canonical import records, transcript
from harness.impl.claude_code.canonical.transcript_user_text import pasted_text


def _same_native_prompt(expected: str, observed: str) -> bool:
    expected_text = expected.strip()
    # Claude Code records a pasted prompt inside its own tag, also in its queue.
    observed_text = pasted_text(observed).strip()
    if not expected_text:
        return False
    if expected_text == observed_text:
        return True
    return _same_image_prompt(expected_text, observed_text)


def _same_image_prompt(expected_text: str, observed_text: str) -> bool:
    attachment_pattern = r'Image attachment "([^"]+)":'
    expected_names = tuple(re.findall(attachment_pattern, expected_text))
    observed_names = tuple(re.findall(attachment_pattern, observed_text))
    if not expected_names or expected_names != observed_names:
        return False
    expected_suffix = expected_text.partition("\n")[2].strip()
    observed_suffix = observed_text.partition("\n")[2].strip()
    return expected_suffix == observed_suffix


def _image_prompt_text(user: records.UserRecord) -> str | None:
    content = None if user.message is None else user.message.content
    if not isinstance(content, list):
        return None
    if not any(isinstance(block, records.ImageBlock) for block in content):
        return None
    texts = _text_blocks(content)
    return "\n".join(texts) if texts else None


def _text_blocks(content: list[records.MessageContentBlock]) -> list[str]:
    return [block.text for block in content if isinstance(block, records.TextBlock) and block.text]


def _user_line_matches(line: bytes, expected: str) -> bool:
    try:
        user_record = records.UserRecord.model_validate_json(line)
    except ValidationError:
        return False
    image_prompt = _image_prompt_text(user_record)
    return image_prompt is not None and _same_native_prompt(expected, image_prompt)


def _sent_prompt_record(parsed: transcript.TranscriptRecord | None, expected: str) -> bool:
    if isinstance(parsed, transcript.PromptTranscriptRecord):
        return not parsed.meta and _same_native_prompt(expected, parsed.text)
    if isinstance(parsed, transcript.SlashCommandTranscriptRecord):
        return _same_native_prompt(expected, parsed.text)
    return False

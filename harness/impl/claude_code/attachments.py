"""Format Claude Code prompts that contain file mentions."""

from __future__ import annotations

from harness.models import AttachmentReference


def prompt_with_attachments(
    text: str,
    attachments: tuple[AttachmentReference, ...],
) -> str:
    """Keep prompt text visible while Claude Code accepts attachment mentions."""
    mentions = " ".join(
        (
            f'Image attachment "{attachment.display_name}": '
            f"{attachment.local_path}"
            if (attachment.media_type or "").startswith("image/")
            else f"@{attachment.local_path}"
        )
        for attachment in attachments
    )
    if not text:
        return mentions
    return text + (f" {mentions}" if mentions else "")


def control_prompt_with_attachments(
    text: str,
    attachments: tuple[AttachmentReference, ...],
) -> str:
    """Put paths before text so the native prompt keeps an exact text suffix."""
    attachment_text = " ".join(
        (
            f'Image attachment "{attachment.display_name}": '
            f"{attachment.local_path}"
            if (attachment.media_type or "").startswith("image/")
            else f"@{attachment.local_path}"
        )
        for attachment in attachments
    )
    if not text:
        return attachment_text
    return attachment_text + (f"\n{text}" if attachment_text else text)

"""Typed attachment references and deterministic marker images."""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from api.application.models.files.upload_response import UploadResponse
from api.controls.models.attachment_reference import AttachmentReferenceBody


def attachment_reference(upload: UploadResponse) -> AttachmentReferenceBody:
    return AttachmentReferenceBody(
        local_path=upload.path,
        display_name=upload.name,
        media_type=upload.mime,
    )


def marker_png(marker: str) -> bytes:
    """Create a large RGB PNG that shows only the supplied decimal digits."""
    if not marker or any(character not in "0123456789" for character in marker):
        raise ValueError("an image marker must contain decimal digits only")
    font = ImageFont.load_default(size=180)
    measure = Image.new("RGB", (1, 1), "white")
    bounds = ImageDraw.Draw(measure).textbbox((0, 0), marker, font=font)
    padding = 48
    width = int(bounds[2] - bounds[0] + 2 * padding)
    height = int(bounds[3] - bounds[1] + 2 * padding)
    image = Image.new("RGB", (width, height), "white")
    ImageDraw.Draw(image).text(
        (padding - bounds[0], padding - bounds[1]),
        marker,
        fill="black",
        font=font,
    )
    result = BytesIO()
    image.save(result, format="PNG", optimize=True)
    return result.getvalue()

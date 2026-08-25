"""Lossless storage codec for raw observation payloads."""

from __future__ import annotations

import zlib

IDENTITY_CODEC = "identity"
ZLIB_CODEC = "zlib"


def stored(payload: bytes) -> tuple[bytes, str]:
    """Return the smaller lossless representation and its codec name."""
    compressed = zlib.compress(payload)
    if len(compressed) >= len(payload):
        return payload, IDENTITY_CODEC
    return compressed, ZLIB_CODEC


def restored(payload: bytes, codec: str) -> bytes:
    """Restore the exact bytes an observer recorded."""
    if codec == IDENTITY_CODEC:
        return payload
    if codec == ZLIB_CODEC:
        return zlib.decompress(payload)
    raise ValueError(f"unknown raw payload codec: {codec}")

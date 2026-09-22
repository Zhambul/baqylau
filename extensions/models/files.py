# Copyright (c) 2026 Zhambyl Yermagambet
"""Describe the exact regular-file bytes used for package identity."""

from dataclasses import dataclass


@dataclass(frozen=True)
class FileBytes:
    """Keep exact bytes and executable permissions in the file identity."""

    digest: str
    byte_length: int
    executable_bits: int

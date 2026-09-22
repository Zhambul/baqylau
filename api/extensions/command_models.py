# Copyright (c) 2026 Zhambyl Yermagambet
"""Typed durable command submissions."""

from pydantic import BaseModel


class ExtensionCommandRequest(BaseModel):
    """Submit one command with its stable request key."""

    scope: str
    request_key: str
    arguments: str
    expected_state_revision: str | None = None

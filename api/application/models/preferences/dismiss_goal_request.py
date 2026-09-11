# Copyright (c) 2026 Zhambyl Yermagambet
"""Identify the completed goal to hide."""

from pydantic import BaseModel, Field


class DismissGoalRequest(BaseModel):
    """Require the objective shown on the requesting client."""

    objective: str = Field(min_length=1)

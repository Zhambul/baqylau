# Copyright (c) 2026 Zhambyl Yermagambet
"""Declare the programs that the host process service may run for a package."""

from typing import Annotated

from pydantic import Field

from baqylau_extension_api.models.base import Identifier, WireModel

MAX_PROCESS_SECONDS = 600.0
MAX_EXECUTABLE_LENGTH = 255
EXECUTABLE_NAME = r"^[A-Za-z0-9][A-Za-z0-9._+-]*$"


class ProcessDeclaration(WireModel):
    """Name one program by a bare executable name and bound its run time."""

    name: Identifier
    executable: Annotated[str, Field(min_length=1, max_length=MAX_EXECUTABLE_LENGTH, pattern=EXECUTABLE_NAME)]
    max_seconds: Annotated[float, Field(gt=0, le=MAX_PROCESS_SECONDS)] = 60.0

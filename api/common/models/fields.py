# Shared field vocabulary for the request models — one owner, no re-encoding.
from typing import Annotated

from pydantic import Field

RequiredText = Annotated[str, Field(min_length=1)]
Scalar = str | int | float | bool | None

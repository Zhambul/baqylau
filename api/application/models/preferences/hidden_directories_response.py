# The updated hidden-directory map.
from collections.abc import Mapping

from pydantic import BaseModel


class HiddenDirectoriesResponse(BaseModel):
    hidden: Mapping[str, float]

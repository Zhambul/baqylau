# The updated hidden-directory map.
from pydantic import BaseModel


class HiddenDirectoriesResponse(BaseModel):
    hidden: dict[str, float]

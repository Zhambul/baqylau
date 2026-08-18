# One harness's menus, composed from the two places its parts honestly live:
# the per-directory catalogue the plugin reads, and the STATIC vocabulary on its
# HarnessInfo. The contract keeps them apart; this is where the browser wants
# them together.
#
# FLAT, because flat is what the page reads (`catalog.commands`,
# `catalog.models`): the route BUILDS one of these from the two sources rather
# than nesting either under a key. This model once declared
# `catalog: HarnessCatalogSnapshot` and so described a nesting that has never
# been on the wire — undetectable while the route hand-built its reply, which
# FastAPI never validates against the declared model.
from pydantic import BaseModel



class EffortOptionResponse(BaseModel):
    value: str
    display_name: str
    default: bool


class ModelOptionResponse(BaseModel):
    model_id: str
    display_name: str
    default: bool
    efforts: tuple[EffortOptionResponse, ...]


class CommandOptionResponse(BaseModel):
    command: str
    description: str
    minimum_prompt_count: int


class RewindModeOptionResponse(BaseModel):
    value: str
    display_name: str


class HarnessCatalogResponse(BaseModel):
    # The commands are discovered by walking the session's own directory, so no
    # static literal can hold them; the models and rewind modes are that literal.
    commands: tuple[CommandOptionResponse, ...]
    models: tuple[ModelOptionResponse, ...]
    rewind_modes: tuple[RewindModeOptionResponse, ...]

# One harness's menus, composed from the two places its parts honestly live:
# the per-directory catalogue the plugin reads, and the STATIC vocabulary on its
# HarnessInfo. The contract keeps them apart; this is where the browser wants
# them together.
#
# FLAT, because flat is what the route sends and what the page reads
# (`catalog.commands`, `catalog.models`): the route merges the snapshot's own
# fields into the reply rather than nesting it under a key. This model declared
# `catalog: HarnessCatalogSnapshot` and so described a nesting that has never been
# on the wire. Nothing caught it, because a route that answers with a JSONResponse
# is never validated against its own response_model — which is what
# test_every_declared_response_model_describes_the_bytes_actually_sent is for.
from pydantic import BaseModel

from harness.models import CommandOption, ModelOption, RewindModeOption


class HarnessCatalogResponse(BaseModel):
    # HarnessCatalogSnapshot's one field: discovered by walking the session's
    # own directory, so no static literal can hold it.
    commands: tuple[CommandOption, ...] = ()
    models: tuple[ModelOption, ...]
    rewind_modes: tuple[RewindModeOption, ...]

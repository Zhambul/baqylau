# One harness's menus, composed from the two places its parts honestly live:
# the per-directory catalogue the plugin reads, and the STATIC vocabulary on its
# HarnessInfo. The contract keeps them apart; this is where the browser wants
# them together.
from pydantic import BaseModel

from harness.models import HarnessCatalogSnapshot, ModelOption, RewindModeOption


class HarnessCatalogResponse(BaseModel):
    catalog: HarnessCatalogSnapshot
    models: tuple[ModelOption, ...]
    rewind_modes: tuple[RewindModeOption, ...]

"""The HTTP layer's own node: the policy this server runs under.

Everything a route needs from the APPLICATION is declared in `app/providers.py`.
This is the one thing that is not the application's — origin admission, the
read-only switch, the boot stamp — and it is declared here rather than there
because `app/` is the composition root and must not import the layer above it.

Same kernel, same scope: one Settings per application, on the application, so a
test overrides the switch instead of reaching for an import-time constant.
"""

from typing import Annotated

from fastapi import Depends

from api.config import Settings, settings
from app.injection import singleton


@singleton
def policy() -> Settings:
    return settings()


Policy = Annotated[Settings, Depends(policy)]

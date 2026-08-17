# Shared field vocabulary for the request models — one owner, no re-encoding.
from typing import Annotated

from fastapi import Path
from pydantic import Field

from api.config import SESSION_ID_PATTERN

RequiredText = Annotated[str, Field(min_length=1)]
Scalar = str | int | float | bool | None

# The two identifiers that arrive in a URL, constrained where they arrive.
#
# Both used to be a bare `str`, so anything at all reached the store, the
# harness registry and — truncated to 200 characters, which is not the same as
# validated — the audit rows a stream writes about itself. Declaring the shape
# here turns that into the 400 the validation handler already renders, and puts
# the constraint in /openapi.yaml where a client can read it.
#
# NOT applied to `actor_id`, deliberately: an actor id is `<session>:lead` or
# whatever a translator read off a subagent, so it has no pattern to pin.
SessionIdPath = Annotated[str, Path(pattern=SESSION_ID_PATTERN.pattern)]

# Harness names are a closed vocabulary, registered at boot from the installed
# plugins; one that fits the shape but is not registered still gets the registry's
# own 404, so this only rejects what could never be a name at all.
HarnessNamePath = Annotated[str, Path(pattern=r"^[a-z][a-z0-9_]*$")]

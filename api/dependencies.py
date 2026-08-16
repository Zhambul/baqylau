# api/dependencies.py — how a route reaches the application graph.
#
# The daemon builds the graph once (api/server.py) and hangs it on the FastAPI
# application's state; every route receives it through this one dependency
# instead of a module global, so tests inject a fixture graph the same way the
# daemon injects the real one.
from typing import Annotated

from fastapi import Depends, Request

from app.bootstrap import CanonicalApplication


def application(request: Request) -> CanonicalApplication:
    return request.app.state.canonical_application


ApplicationGraph = Annotated[CanonicalApplication, Depends(application)]

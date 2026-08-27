"""The daemon's injection kernel: three names, and no state of its own.

The dependency graph lives in the providers' SIGNATURES (`app/providers.py`), which is what
FastAPI reads to build a route's dependencies — so the graph is declared once and
resolved by the framework, not assembled by hand into an object every consumer
then reaches into.

`singleton` is the whole point of this module. Every node in the graph is one
instance per process (a database handle initialises once, a service holding a
warm cache must hold ONE), but the memo is NOT a module-level dict: it hangs off
the application object, keyed by the provider. So a second application in the
same interpreter — the next test — gets its own instances, and nothing survives
the app that owns it.

`resolve` is the same graph for callers that have no request: the lifespan's
background threads and the CLI. It reads the same annotations FastAPI reads and
shares the same registry, so a worker and a route hold the SAME service object.

Providers must not use postponed annotations (`from __future__ import
annotations`): FastAPI evaluates a signature against the callable's
`__globals__`, and after decoration those are this module's, not the provider
module's. Real annotation objects sidestep the question entirely.
"""

import functools
import inspect
from typing import (
    Annotated,
    Any,
    Callable,
    Concatenate,
    ParamSpec,
    TypeVar,
    get_args,
    get_origin,
    get_type_hints,
)

from fastapi import Request
from fastapi.params import Depends

# Provider -> the one instance built from it. Held on `app.state.instances` for
# the HTTP path and passed explicitly everywhere else; never module-level.
Instances = dict[Any, Any]

T = TypeVar("T")
P = ParamSpec("P")


class TypeHint:
    """One provider parameter's own type-hint value: a class, `X | Y`, a
    generic alias, or an `Annotated[...]` wrapper. `typing` itself has no
    sharper name for this — `get_type_hints` is stubbed to return
    `dict[str, Any]` — so this exists only so a reader of `_dependency_of`
    sees WHAT crosses the boundary instead of the bare admission that
    anything might. Never instantiated: every real value handed in already
    satisfies it, because `Any` satisfies everything."""


def registry() -> Instances:
    """A fresh singleton scope. One per application, one per test."""
    instances: Instances = {}
    return instances


def singleton(build: Callable[P, T]) -> Callable[Concatenate[Request, P], T]:
    """One instance per application, built on first use, memoised on the app.

    The returned provider takes a `Request` on top of what `build` declares —
    that is how it reaches the registry — and FastAPI fills both it and the
    declared dependencies in.
    """
    signature = inspect.signature(build)
    parameters = (
        inspect.Parameter("request", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Request),
        *signature.parameters.values(),
    )

    @functools.wraps(build)
    def provider(
        request: Request,
        *args: P.args,
        **dependencies: P.kwargs,
    ) -> T:
        instances: Instances = request.app.state.instances
        if build not in instances:
            instances[build] = build(*args, **dependencies)
        value: T = instances[build]
        return value

    # Keyed by `build`, not by `provider`, so `resolve` below and this path put
    # the same object in the same slot.
    provider.__signature__ = signature.replace(parameters=parameters)  # type: ignore[attr-defined]
    provider.build = build  # type: ignore[attr-defined]
    # `functools.wraps` types its result as wrapping `build`'s OWN signature
    # (`P`), not `provider`'s declared `[Request, **P]` — it exists here for
    # the runtime `__name__`/`__doc__`/`__wrapped__` copy, not for typing.
    return provider  # type: ignore[return-value]


def resolve(instances: Instances, provider: Callable[..., T]) -> T:
    """Build `provider` outside a request, from the same declarations.

    Depth-first over the `Annotated[T, Depends(...)]` parameters, memoised in the
    registry the caller owns — so a background worker resolved here and a route
    resolved by FastAPI hold one and the same instance.
    """
    build = getattr(provider, "build", provider)
    if build in instances:
        already: T = instances[build]
        return already
    hints = get_type_hints(build, include_extras=True)
    dependencies = {
        name: resolve(instances, _dependency_of(name, hints[name]))
        for name in inspect.signature(build).parameters
    }
    built: T = build(**dependencies)
    instances[build] = built
    return built


def _dependency_of(
    name: str,
    type_hint: TypeHint,
) -> Callable[..., Any]:
    """The provider an `Annotated[T, Depends(provider)]` parameter names."""
    if get_origin(type_hint) is Annotated:
        for extra in get_args(type_hint)[1:]:
            if isinstance(extra, Depends) and extra.dependency is not None:
                return extra.dependency
    raise TypeError("parameter %r declares no provider: %r" % (name, type_hint))

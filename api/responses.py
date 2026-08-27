# api/responses.py — the OpenAPI response vocabulary every router shares.
#
# `response_model=` and the return annotation say what a route answers on its way
# out (the architecture suite requires one of the two). These say the rest, which
# /openapi.yaml previously claimed did not exist at all: the two statuses ANY
# request can end in, the four refusals the control-plane guard issues before a
# handler runs, and the outcome statuses the control and pane planes really
# return. A published document that describes only the happy path is not a
# contract, and this is the layer that knows the others.
from __future__ import annotations

from types import UnionType

from pydantic import BaseModel

from api.common.models.replies.error_response import ErrorResponse

# A route's body model, or a `|` union of several — a control gesture answers
# with one of five outcome models, so the union itself is the documented shape.
ResponseModel = type[BaseModel] | UnionType

Documented = dict[int | str, dict[str, ResponseModel | str]]


def errors(statuses: dict[int, str]) -> Documented:
    """Statuses answered with this server's one error body."""
    documented: Documented = {
        status: {"model": ErrorResponse, "description": description}
        for status, description in statuses.items()
    }
    return documented


def with_body(model: ResponseModel, statuses: dict[int, str]) -> Documented:
    """Statuses answered with a route's OWN body model.

    A rejected control is a ControlOutcome and a refused launch is a
    LaunchResult — the status is the verdict, the body is unchanged. Without this
    the schema described those as untyped, or as the error shape they deliberately
    are not.
    """
    documented: Documented = {
        status: {"model": model, "description": description}
        for status, description in statuses.items()
    }
    return documented


# Registered on the application itself, so every route carries them: the two
# answers api/app.py's exception handlers can produce for any request at all.
EVERY_ROUTE = errors({
    400: "The request names something unknown, or cannot be acted on as posed.",
    500: "An internal failure. Audited as an `errors` row; the body says nothing more.",
})

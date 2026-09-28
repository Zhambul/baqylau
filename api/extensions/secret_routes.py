# Copyright (c) 2026 Zhambyl Yermagambet
"""Report, store, and clear declared extension secret values through write-only routes."""

from http import HTTPStatus

from baqylau_extension_api.models.base import ExtensionId, Identifier
from fastapi import APIRouter, Depends, HTTPException, Response

from api.extensions import secret_models
from api.extensions.admission import require_extension_json
from app.provider_extension_settings import SecretControlService as SecretService
from extensions.secret_control import SecretControl, SecretNotDeclaredError

router = APIRouter()


@router.get("/api/extensions/{extension_id}/secrets")
def extension_secrets(
    extension_id: ExtensionId, control: SecretService, response: Response,
) -> secret_models.ExtensionSecretsResponse:
    """Report each declared secret reference and whether it has a value.

    Returns:
        The reference states; no value.

    """
    response.headers["Cache-Control"] = "no-store"
    return _states(control, extension_id)


@router.put("/api/extensions/{extension_id}/secrets/{name}", dependencies=[Depends(require_extension_json)])
def store_extension_secret(
    extension_id: ExtensionId, name: Identifier, secret_write_request: secret_models.SecretWriteRequest,
    control: SecretService,
) -> secret_models.ExtensionSecretsResponse:
    """Store or replace one declared secret value.

    Returns:
        The reference states after the change; no value.

    Raises:
        HTTPException: If the package does not declare the name.

    """
    try:
        control.write(extension_id, name, secret_write_request.secret)
    except SecretNotDeclaredError as error:
        raise HTTPException(HTTPStatus.NOT_FOUND, str(error)) from error
    return _states(control, extension_id)


@router.delete("/api/extensions/{extension_id}/secrets/{name}")
def clear_extension_secret(
    extension_id: ExtensionId, name: Identifier, control: SecretService,
) -> secret_models.ExtensionSecretsResponse:
    """Clear one declared secret value.

    Returns:
        The reference states after the change.

    Raises:
        HTTPException: If the package does not declare the name.

    """
    try:
        control.delete(extension_id, name)
    except SecretNotDeclaredError as error:
        raise HTTPException(HTTPStatus.NOT_FOUND, str(error)) from error
    return _states(control, extension_id)


def _states(control: SecretControl, extension_id: str) -> secret_models.ExtensionSecretsResponse:
    try:
        statuses = control.statuses(extension_id)
    except SecretNotDeclaredError as error:
        raise HTTPException(HTTPStatus.NOT_FOUND, str(error)) from error
    return secret_models.ExtensionSecretsResponse(
        secrets=tuple(secret_models.SecretStatusResponse(
            name=status.name, required=status.required, configured=status.configured,
        ) for status in statuses),
        read_only=control.policy.read_only,
    )

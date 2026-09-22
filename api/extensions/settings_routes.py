# Copyright (c) 2026 Zhambyl Yermagambet
"""Read one settings scope and prepare revision-checked replacement values."""

from http import HTTPStatus

from baqylau_extension_api.models.base import ExtensionId
from fastapi import APIRouter, Depends, Response

from api.extensions import lifecycle_models, settings_models
from api.extensions.admission import require_extension_json
from api.extensions.lifecycle_admission import admission_response
from api.extensions.lifecycle_routes import LIFECYCLE_RESPONSES
from api.extensions.settings_dependencies import SettingsSelection
from app.provider_extension_controls import ControlPolicy
from app.provider_extension_settings import SettingsControlService

router = APIRouter(responses=LIFECYCLE_RESPONSES)


@router.get("/api/extensions/{extension_id}/settings")
def read_extension_settings(
    extension_id: ExtensionId, control: SettingsControlService, policy: ControlPolicy, response: Response,
    selection: SettingsSelection,
) -> settings_models.ExtensionSettingsResponse:
    """Read accepted values; a pending operation does not replace this result.

    Returns:
        The selected ordinary settings, schema, revisions, and write policy.

    """
    response.headers["Cache-Control"] = "no-store"
    snapshot = control.read_settings(extension_id, selection)
    return settings_models.ExtensionSettingsResponse(settings=snapshot, read_only=policy.read_only)


@router.put(
    "/api/extensions/{extension_id}/settings", dependencies=[Depends(require_extension_json)],
    status_code=HTTPStatus.ACCEPTED,
)
def change_extension_settings(
    extension_id: ExtensionId, settings_change_request: settings_models.SettingsChangeRequest,
    control: SettingsControlService,
) -> lifecycle_models.LifecycleAdmissionResponse:
    """Admit an exact settings request through normal runtime preparation.

    Returns:
        The accepted or replayed operation; it must finish before values change.

    """
    return admission_response(control.change_settings(extension_id, settings_change_request))

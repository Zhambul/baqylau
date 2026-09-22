# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide http test contracts."""

from __future__ import annotations

from api.common.models.replies.error_response import ErrorResponse
from tests import (
    http_library_dependencies as library_dependencies,
    http_test_application_builders,
    http_test_audit_models,
    http_test_controls,
    http_test_pane_models,
    http_value_dependencies as standard_dependencies,
)

REPOSITORY_ROOT = str(library_dependencies.Path(__file__).resolve().parents[1])
type JsonValue = bool | float | int | str | list[JsonValue] | dict[str, JsonValue] | None


def assert_recorded_telemetry(
    server: http_test_pane_models.RunningDaemon, path: str, document: dict[str, JsonValue],
) -> None:
    """Check that a telemetry request is accepted and recorded."""
    response = http_test_controls.post(server, path, document)
    assert response.status == library_dependencies.http.client.OK
    assert response.body.json == {"recorded": True}


def assert_legacy_telemetry_routes_are_gone(server: http_test_pane_models.RunningDaemon) -> None:
    """Check that removed telemetry routes return not found."""
    for legacy_path in (
        "/api/session/session-one/hint-audit",
        "/api/session/session-one/client-fail",
        "/api/clientlog",
    ):
        status, _ = http_test_controls.post(server, legacy_path, {})
        assert status == library_dependencies.http.client.NOT_FOUND


def validate_route_response(
    server: http_test_pane_models.RunningDaemon, route: library_dependencies.fastapi.routing.APIRoute, path: str,
) -> None:
    """Validate the response for this request-only application.

    Extension state needs a daemon owner. Private-daemon lifecycle tests check
    its successful response; this fixture must return the declared refusal.
    """
    status, _, body = http_test_controls.get_response(server, path)
    if path == "/api/extensions/state":
        assert status == library_dependencies.http.client.SERVICE_UNAVAILABLE, (path, body)
        ErrorResponse.model_validate_json(body.raw)
        return
    assert status == library_dependencies.http.client.OK, (path, body)
    library_dependencies.TypeAdapter(route.response_model).validate_json(body.raw)


def assert_plane_security(server: http_test_pane_models.RunningDaemon) -> None:
    """Check security headers on pages, API responses, errors, and streams."""
    for route_path, read_body in (
        ("/", True),
        ("/api/sessions", True),
        ("/api/nothing-is-here", True),
        ("/api/stream", False),
    ):
        _, headers, _ = http_test_controls.get_response(server, route_path, read_body=read_body)
        http_test_application_builders.assert_security_policy(headers, route_path)


def assert_cached_build_headers(server: http_test_pane_models.RunningDaemon) -> None:
    """Check immutable cache and content-type protection headers on built assets."""
    manifest_path = library_dependencies.Path(REPOSITORY_ROOT) / "dashboard/static/build/.vite/manifest.json"
    manifest = standard_dependencies.json.loads(manifest_path.read_text(encoding="utf-8"))["src/main.ts"]
    _, headers, _ = http_test_controls.get_response(server, f"/static/build/{manifest['file']}")
    assert headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert headers["X-Content-Type-Options"] == "nosniff"


def index_document(server: http_test_pane_models.RunningDaemon) -> bytes:
    """Read and check the dashboard index response.

    Returns:
        The index HTML bytes.

    """
    status, content_type, document = http_test_controls.get(server, "/")
    assert status == library_dependencies.http.client.OK
    assert content_type == "text/html; charset=utf-8"
    return document.raw


def assert_served_asset(
    server: http_test_pane_models.RunningDaemon, reference: http_test_audit_models.AssetReference,
) -> None:
    """Check that an asset is served with content and the expected media type."""
    status, content_type, body = http_test_controls.get(server, reference.path)
    assert status == library_dependencies.http.client.OK, reference.path
    assert body, reference.path
    assert content_type is not None
    assert content_type.startswith(http_test_application_builders.expected_content_type(reference.suffix)), (
        reference.path,
        content_type,
    )

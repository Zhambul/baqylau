# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide http test response helpers."""

from __future__ import annotations

from tests import (
    http_contract_dependencies as contract_dependencies,
    http_library_dependencies as library_dependencies,
    http_runtime_dependencies as runtime_dependencies,
    http_value_dependencies as standard_dependencies,
)

SESSION_ID_TEXT = "session-one"
CODEX_HARNESS_TEXT = "codex"
SESSION_ID_FIELD = "session_id"
BROWSER_DEVICE_ID = "browser-one"
DEVICE_ID_FIELD = "device_id"
HOOK_SESSION_ID = "hook-session"
SESSION_ID = runtime_dependencies.domain_ids.SessionId(SESSION_ID_TEXT)
type JsonValue = bool | float | int | str | list[JsonValue] | dict[str, JsonValue] | None
type MethodAuditRow = tuple[str, contract_dependencies.control_services.ControlAudit]
type ControlInvocation = tuple[
    standard_dependencies.collections_abc.Callable[[], runtime_dependencies.control_models.ControlOutcome],
    runtime_dependencies.control_models.ControlRequest,
]
_FIXTURE_PATH_PARAMETERS = library_dependencies.MappingProxyType({
    SESSION_ID_FIELD: str(SESSION_ID),
    "harness": CODEX_HARNESS_TEXT,
})
# Routes whose required query has a known test value; the root directory is in no repository.
_FIXTURE_QUERIES = library_dependencies.MappingProxyType({
    "/api/extension-web/repository-scope": "?directory=%2F",
})


class JsonBody:
    """Represent a response body that can decode its JSON document."""

    def __init__(self, raw: bytes = b"") -> None:
        """Store the response bytes."""
        self._raw = raw

    @property
    def json(self) -> library_dependencies.typing.Any:
        """The response JSON document."""
        return standard_dependencies.json.loads(self._raw)

    @property
    def raw(self) -> bytes:
        """The response bytes."""
        return self._raw

    def decode(self) -> str:
        """Decode the response bytes as text.

        Returns:
            The response body decoded as UTF-8 text.

        """
        return self._raw.decode()

    def __bool__(self) -> bool:
        """Return whether the response body has data.

        Returns:
            Whether the response body has data.

        """
        return bool(self._raw)


def subscription_document(endpoint: str, public_key: str, secret: str) -> dict[str, JsonValue]:
    """Build a subscription request for the test browser device.

    Returns:
        The subscription keys and fixed device details.

    """
    return {
        "subscription": {"endpoint": endpoint, "keys": {"p256dh": public_key, "auth": secret}},
        DEVICE_ID_FIELD: BROWSER_DEVICE_ID,
        "device_label": "Tablet",
    }


def assert_control_invocations(rows: list[MethodAuditRow], calls: list[ControlInvocation]) -> None:
    """Check that each control call adds one acknowledged audit record."""
    for before, (invoke, request) in enumerate(calls):
        invoke()
        assert len(rows) == before + 1
        action, content = rows[-1]
        assert action == "control"
        assert content.control == request.control_name
        assert content.status == "acknowledged"


def sse_line(response: library_dependencies.http.client.HTTPResponse) -> str:
    """Return one decoded SSE line.

    Returns:
        One decoded SSE line.

    """
    return response.readline().decode().rstrip("\n")


def sse_event_values(
    line: str, event: str | None, event_payload: JsonValue | None,
) -> tuple[str | None, JsonValue | None]:
    """Return SSE event values after one non-empty line.

    Returns:
        SSE event values after one non-empty line.

    """
    if line.startswith("event: "):
        return (line.removeprefix("event: "), event_payload)
    if line.startswith("data: "):
        return (event, standard_dependencies.json.loads(line.removeprefix("data: ")))
    return (event, event_payload)


def pre_tool_hook_payload(tmp_path: library_dependencies.Path) -> bytes:
    """Build a pre-tool hook for a shell command in the test directory.

    Returns:
        The JSON hook document encoded as bytes.

    """
    return standard_dependencies.json.dumps({
        SESSION_ID_FIELD: HOOK_SESSION_ID,
        "transcript_path": str(tmp_path / "hook-session.jsonl"),
        "cwd": str(tmp_path),
        "hook_event_name": "PreToolUse",
        "hook_event_id": "pre-one",
        "tool_name": "Bash",
        "tool_use_id": "tool-one",
        "tool_input": {"command": "printf hello"},
    }).encode()


def fixture_route_path(route: library_dependencies.fastapi.routing.APIRoute) -> str | None:
    """Fill a route path with the known test parameters.

    Returns:
        The completed path, or None if a required parameter is unknown.

    """
    try:
        path = route.path.format(**_FIXTURE_PATH_PARAMETERS)
    except KeyError:
        return None
    return f"{path}{_FIXTURE_QUERIES.get(route.path, '')}"

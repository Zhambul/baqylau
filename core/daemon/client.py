"""The thin HTTP door terminal-side clients use to reach the running daemon.

Every process outside the daemon that used to build the application graph is a
client of this module instead: the pane renderers stream frames, the keybinding
and click handlers post gestures. There is deliberately NO fallback to a direct
store read — the daemon is the one interpreter, and a client that cannot reach
it says so.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from core.daemon.contract import HOST_ADDRESS, PORT_NUMBER, POST_HEADER

BASE_URL = f"http://{HOST_ADDRESS}:{PORT_NUMBER}"
REQUEST_TIMEOUT_SECONDS = 10.0
RECONNECT_DELAY_SECONDS = 2.0
# The server ticks a pane stream several times a second; a read that stalls
# this long means the daemon died without closing the socket — reconnect.
STREAM_STALL_SECONDS = 10.0
NOT_RUNNING_MESSAGE = (
    f"baqylau dashboard is not reachable on {BASE_URL} — start it with "
    "`python3 bin/baqylau-dashboard.py start`"
)


class DaemonUnreachable(OSError):
    def __init__(self) -> None:
        super().__init__(NOT_RUNNING_MESSAGE)


def get_text(path: str) -> str:
    try:
        with urllib.request.urlopen(
            BASE_URL + path, timeout=REQUEST_TIMEOUT_SECONDS
        ) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        raise RuntimeError(_error_message(error.read())) from error
    except urllib.error.URLError as error:
        raise DaemonUnreachable() from error


def post_json(path: str, body: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        BASE_URL + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", POST_HEADER: "1"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        payload = error.read()
        try:
            return error.code, json.loads(payload or b"{}")
        except ValueError:
            return error.code, {"error": payload.decode("utf-8", "replace")}
    except urllib.error.URLError as error:
        raise DaemonUnreachable() from error


def post_bytes(
    path: str,
    body: bytes,
    headers: dict[str, str],
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> tuple[int, bytes]:
    """POST exact bytes (a hook delivery, not a JSON envelope) and return the
    response verbatim — the caller owns both encodings."""
    request = urllib.request.Request(
        BASE_URL + path,
        data=body,
        headers={**headers, POST_HEADER: "1"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except urllib.error.URLError as error:
        raise DaemonUnreachable() from error


def _error_message(payload: bytes) -> str:
    try:
        return json.loads(payload or b"{}").get("error") or "request failed"
    except ValueError:
        return payload.decode("utf-8", "replace") or "request failed"


def sse_events(response):
    """Decode one SSE response into (event, data) pairs.

    Comment lines (the server's idle ticks) are surfaced as ("tick", None) so a
    pane client gets a beat several times a second to check its own terminal
    size against, even when nothing is being painted."""
    event = None
    data_lines: list[str] = []
    for raw_line in response:
        line = raw_line.decode("utf-8").rstrip("\n")
        if line.startswith(":"):
            yield "tick", None
            continue
        if not line:
            if event is not None or data_lines:
                yield event or "message", "\n".join(data_lines)
            event = None
            data_lines = []
            continue
        if line.startswith("event: "):
            event = line[len("event: "):]
        elif line.startswith("data: "):
            data_lines.append(line[len("data: "):])


def pane_frames(session_id: str, kind: str):
    """Yield ANSI frames for one pane stream, forever.

    Owns the whole client loop: connect at the current terminal width, repaint
    from `frame` events, reconnect immediately when the terminal is resized
    (the server re-renders its shared model at the new width) and after
    RECONNECT_DELAY_SECONDS when the daemon is unreachable or restarting."""
    while True:
        width = shutil.get_terminal_size().columns
        path = (
            f"/api/sessions/{urllib.parse.quote(session_id)}"
            f"/panes/{kind}/stream?width={width}"
        )
        resized = False
        try:
            with urllib.request.urlopen(
                BASE_URL + path, timeout=STREAM_STALL_SECONDS
            ) as response:
                for event, data in sse_events(response):
                    if event == "session":
                        session_id = json.loads(data)["session_id"]
                    elif event == "frame":
                        yield json.loads(data)["ansi"]
                    elif event == "error":
                        break
                    if shutil.get_terminal_size().columns != width:
                        resized = True
                        break
        except (OSError, ValueError):
            pass
        if not resized:
            time.sleep(RECONNECT_DELAY_SECONDS)


def run_pane(session_id: str, kind: str) -> None:
    """The pane process body: copy streamed frames to this terminal."""
    for ansi in pane_frames(session_id, kind):
        sys.stdout.write(ansi)
        sys.stdout.flush()

# Copyright (c) 2026 Zhambyl Yermagambet
"""Call the server that saved a native event."""

import subprocess  # noqa: S404 -- Use the installed native API client.
from ipaddress import ip_address
from urllib.parse import urlencode

import psutil

from harness.impl.opencode2.records import NativeRecord

REQUEST_TIMEOUT_SECONDS = 10
ERRORS = (OSError, ValueError, psutil.Error, subprocess.SubprocessError)


def request(native_record: NativeRecord, method: str, path: str, body: str) -> bytes:
    """Use the native server identity without saving its password.

    Returns:
        The native response body.

    Raises:
        ValueError: If the original native server cannot be identified.

    """
    if native_record.server_process_id is None or native_record.event.created is None:
        message = "The native event has no server identity"
        raise ValueError(message)
    process = psutil.Process(native_record.server_process_id)
    if (
        "serve" not in process.cmdline()
        or process.create_time() > native_record.event.created / 1000
    ):
        message = "The native server identity changed"
        raise ValueError(message)
    query = urlencode((("location[directory]", native_record.session.location.directory),))
    arguments = (
        process.exe(), "api", *_arguments(process), method,
        f"{path}?{query}", "--data", body,
    )
    response = subprocess.run(  # noqa: S603 -- Fixed native API client; the password stays in its environment.
        arguments,
        env=process.environ(), capture_output=True, check=True, timeout=REQUEST_TIMEOUT_SECONDS,
    )
    return response.stdout


def _endpoint(process: psutil.Process) -> str:
    listeners = tuple(
        connection.laddr for connection in process.net_connections("inet")
        if connection.status == psutil.CONN_LISTEN and ip_address(connection.laddr.ip).is_loopback
    )
    if len(listeners) != 1:
        message = "The native server has no unique local endpoint"
        raise ValueError(message)
    address = listeners[0]
    host = f"[{address.ip}]" if ":" in address.ip else address.ip
    return f"http://{host}:{address.port}"


def _arguments(process: psutil.Process) -> tuple[str, ...]:
    endpoint = _endpoint(process)
    if "--service" not in process.cmdline():
        return ("--server", endpoint)
    status = subprocess.run(  # noqa: S603 -- Read the native service profile before using its credentials.
        (process.exe(), "service", "status"), env=process.environ(),
        capture_output=True, check=True, timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if status.stdout.decode().strip() != endpoint:
        message = "The native service profile names another server"
        raise ValueError(message)
    return ()

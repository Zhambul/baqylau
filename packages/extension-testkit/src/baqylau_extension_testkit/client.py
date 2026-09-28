# Copyright (c) 2026 Zhambyl Yermagambet
"""Read and send typed host requests; the process owner is a separate class."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
from pydantic import BaseModel

from baqylau_extension_testkit.waiting import wait_until

if TYPE_CHECKING:
    from baqylau_extension_testkit.host_process import HostProcess

REQUEST_SECONDS = 30.0
READY_SECONDS = 60.0
HEALTH_PATH = "/api/health"


class HostRequestError(RuntimeError):
    """Report a host reply with a status that the request does not accept."""


class HostClient:
    """Send typed JSON requests to one host URL."""

    def __init__(self, url: str, network: httpx.BaseTransport | None = None) -> None:
        """Open one HTTP connection pool for the host; a test can give its own network transport."""
        self.transport = httpx.Client(base_url=url, timeout=REQUEST_SECONDS, transport=network)

    def read[Reply: BaseModel](self, path: str, reply: type[Reply]) -> Reply:
        """Send a GET request and check the reply.

        Returns:
            The checked reply.

        """
        return reply.model_validate_json(self._checked(self.transport.get(path)).content)

    def send[Reply: BaseModel](self, path: str, body: BaseModel, reply: type[Reply]) -> Reply:
        """Send a JSON POST request and check the reply.

        Returns:
            The checked reply.

        """
        response = self.transport.post(
            path, content=body.model_dump_json(), headers={"Content-Type": "application/json"},
        )
        return reply.model_validate_json(self._checked(response).content)

    def wait_until_ready(self, process: HostProcess) -> None:
        """Wait until the host answers its health route."""
        wait_until(lambda: self._answered(process), READY_SECONDS, lambda: _not_ready(process))

    def close(self) -> None:
        """Close the connection pool."""
        self.transport.close()

    def _answered(self, process: HostProcess) -> bool | None:
        if not process.running():
            tail = process.log_tail()
            message = f"the host ended before it was ready\n{tail}"
            raise HostRequestError(message)
        try:
            return self.transport.get(HEALTH_PATH).is_success or None
        except httpx.TransportError:
            return None

    def _checked(self, response: httpx.Response) -> httpx.Response:
        if not response.is_success:
            request = response.request
            path, status = request.url.path, response.status_code
            detail = response.text
            message = f"{request.method} {path} returned {status}\n{detail}"
            raise HostRequestError(message)
        return response


def _not_ready(process: HostProcess) -> str:
    url, log = process.url, process.log_tail()
    return f"the host at {url} is not ready\n{log}"

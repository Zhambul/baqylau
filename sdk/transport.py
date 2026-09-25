# Copyright (c) 2026 Zhambyl Yermagambet
"""Typed HTTP transport for the Baqylau API."""

from __future__ import annotations

from contextlib import contextmanager
from http import HTTPStatus
from types import MappingProxyType
from typing import TYPE_CHECKING

import httpx
from pydantic import BaseModel, TypeAdapter, ValidationError

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

JSON_HEADERS = MappingProxyType({"Content-Type": "application/json"})
ERROR_BODY_CHARACTER_LIMIT = 400


class ApiFailureError(RuntimeError):
    """Represent API failure."""


def _decode_response[TransportResultT](
    method: str,
    path: str,
    response: httpx.Response,
    adapter: TypeAdapter[TransportResultT],
    accepted_statuses: set[int],
) -> TransportResultT:
    if response.status_code not in accepted_statuses:
        message = _failure_message(method, path, response.status_code, response.text)
        raise ApiFailureError(message)
    try:
        return adapter.validate_json(response.content)
    except ValidationError as error:
        message = f"{method} {path} returned an invalid document: {error}"
        raise ApiFailureError(message) from error


class HttpTransport:
    """Represent HTTP transport."""

    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        """Initialize the object."""
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(base_url=self.base_url, timeout=timeout)

    def close(self) -> None:
        """Close close."""
        self.client.close()

    def get[TransportResultT](self, path: str, adapter: TypeAdapter[TransportResultT]) -> TransportResultT:
        """Return get.

        Returns:
            Get.

        """
        response = self.client.get(path)
        return _decode_response("GET", path, response, adapter, {HTTPStatus.OK})

    def post[TransportResultT](
        self,
        path: str,
        document: BaseModel,
        adapter: TypeAdapter[TransportResultT],
        accepted_statuses: set[int],
        *,
        timeout: float | None = None,
    ) -> tuple[int, TransportResultT]:
        """Post.

        Returns:
            Result items.

        """
        response = (
            self.client.post(
                path,
                content=document.model_dump_json(by_alias=True),
                headers=JSON_HEADERS,
            )
            if timeout is None
            else self.client.post(
                path,
                content=document.model_dump_json(by_alias=True),
                headers=JSON_HEADERS,
                timeout=timeout,
            )
        )
        return response.status_code, _decode_response(
            "POST",
            path,
            response,
            adapter,
            accepted_statuses,
        )

    def put[TransportResultT](
        self, path: str, document: BaseModel, adapter: TypeAdapter[TransportResultT], accepted_statuses: set[int],
    ) -> tuple[int, TransportResultT]:
        """Send a complete typed replacement with the normal JSON admission headers.

        Returns:
            The HTTP status and checked response document.

        """
        response = self.client.put(
            path, content=document.model_dump_json(by_alias=True), headers=JSON_HEADERS,
        )
        return response.status_code, _decode_response("PUT", path, response, adapter, accepted_statuses)

    def delete[TransportResultT](
        self, path: str, adapter: TypeAdapter[TransportResultT], accepted_statuses: set[int],
    ) -> tuple[int, TransportResultT]:
        """Remove one resource and read the checked response document.

        Returns:
            The HTTP status and checked response document.

        """
        response = self.client.delete(path)
        return response.status_code, _decode_response("DELETE", path, response, adapter, accepted_statuses)

    @contextmanager
    def event_stream(
        self,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> Iterator[Iterator[str]]:
        """Return the event stream.

        Yields:
            Iterator over decoded response lines.

        Raises:
            ApiFailureError: If the API request fails.

        """
        with self.client.stream("GET", path, headers=headers) as response:
            if response.status_code != HTTPStatus.OK:
                message = _failure_message(
                    "GET",
                    path,
                    response.status_code,
                    response.read().decode(errors="replace"),
                )
                raise ApiFailureError(
                    message,
                )
            media_type = response.headers.get("content-type", "")
            if not media_type.startswith("text/event-stream"):
                message = f"GET {path} returned content type {media_type!r}"
                raise ApiFailureError(
                    message,
                )
            yield response.iter_lines()


def _failure_message(method: str, path: str, status_code: int, response_body: str) -> str:
    body = response_body[:ERROR_BODY_CHARACTER_LIMIT]
    return f"{method} {path} returned {status_code}: {body}"

"""Typed HTTP transport for the Baqylau API."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TypeVar

import httpx
from pydantic import TypeAdapter, ValidationError

T = TypeVar("T")


class ApiFailure(RuntimeError):
    pass


class HttpTransport:
    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(base_url=self.base_url, timeout=timeout)

    def close(self) -> None:
        self.client.close()

    def get(self, path: str, adapter: TypeAdapter[T]) -> T:
        response = self.client.get(path)
        return self._decode("GET", path, response, adapter, {200})

    def post(
        self,
        path: str,
        document: object,
        adapter: TypeAdapter[T],
        accepted_statuses: set[int],
        *,
        timeout: float | None = None,
    ) -> tuple[int, T]:
        response = (
            self.client.post(path, json=document)
            if timeout is None
            else self.client.post(path, json=document, timeout=timeout)
        )
        return response.status_code, self._decode(
            "POST", path, response, adapter, accepted_statuses
        )

    @contextmanager
    def event_stream(
        self,
        path: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> Iterator[Iterator[str]]:
        with self.client.stream("GET", path, headers=headers) as response:
            if response.status_code != 200:
                raise ApiFailure(
                    f"GET {path} returned {response.status_code}: "
                    f"{response.read().decode(errors='replace')[:400]}"
                )
            media_type = response.headers.get("content-type", "")
            if not media_type.startswith("text/event-stream"):
                raise ApiFailure(
                    f"GET {path} returned content type {media_type!r}"
                )
            yield response.iter_lines()

    @staticmethod
    def _decode(
        method: str,
        path: str,
        response: httpx.Response,
        adapter: TypeAdapter[T],
        accepted_statuses: set[int],
    ) -> T:
        if response.status_code not in accepted_statuses:
            raise ApiFailure(
                f"{method} {path} returned {response.status_code}: {response.text[:400]}"
            )
        try:
            return adapter.validate_json(response.content)
        except ValidationError as error:
            raise ApiFailure(f"{method} {path} returned an invalid document: {error}") from error

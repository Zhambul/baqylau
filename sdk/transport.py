"""Typed HTTP transport for the Baqylau API."""

from __future__ import annotations

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
    ) -> tuple[int, T]:
        response = self.client.post(path, json=document)
        return response.status_code, self._decode(
            "POST", path, response, adapter, accepted_statuses
        )

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


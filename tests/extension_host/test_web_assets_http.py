# Copyright (c) 2026 Zhambyl Yermagambet
"""List active web views and serve only their declared assets by package digest (C18, C25)."""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING

import httpx

from tests.extension_host import lifecycle_http_fixture as lifecycle, package_fixture, process_fixture

if TYPE_CHECKING:
    from pathlib import Path

    from sdk.client import BaqylauClient

OWNER = package_fixture.OWNER
IMMUTABLE = "public, max-age=31536000, immutable"
DIGEST_LENGTH = 64
OTHER_DIGEST = "0" * DIGEST_LENGTH


@dataclass(frozen=True)
class WebReads:
    """Keep the listed view, the module response, and the refused statuses."""

    view_id: str
    module: httpx.Response
    refused: set[int]


def enable(client: BaqylauClient) -> None:
    """Enable the fixture web package and wait for the operation."""
    request = lifecycle.lifecycle_request(client, OWNER, "enable", "enable-web")
    admitted = client.extensions.lifecycle.change(OWNER, request)
    lifecycle.wait_operation(client, admitted.operation.operation_id)


def read_views(http: httpx.Client) -> WebReads:
    """Read the first view and its module, and try an undeclared file, a path escape, and a wrong digest.

    Returns:
        What the daemon answered.

    """
    view = http.get("/api/extension-web/views").json()["views"][0]
    module_url = view["module_url"]
    base = module_url.rsplit("/", 1)[0]
    wrong = (
        f"{base}/extension.json",
        f"{base}/../extension.json",
        module_url.replace(view["package_digest"], OTHER_DIGEST),
    )
    refused = {http.get(url).status_code for url in wrong}
    return WebReads(view["view_id"], http.get(module_url), refused)


def test_module_loads_from_its_digest(tmp_path: Path) -> None:
    """The module URL returns the declared bytes, with immutable caching; other files are not served."""
    package_fixture.write_package(tmp_path / "packages", web=True)
    with process_fixture.running_catalog(tmp_path) as client:
        enable(client)
        reads = read_views(client.transport.client)

    assert reads.view_id == f"{OWNER}.main"
    assert (reads.module.status_code, reads.module.content) == (HTTPStatus.OK, package_fixture.WEB_SOURCE)
    assert reads.module.headers["content-type"].startswith("text/javascript")
    assert reads.module.headers["cache-control"] == IMMUTABLE
    assert reads.refused == {HTTPStatus.NOT_FOUND}

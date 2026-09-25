# Copyright (c) 2026 Zhambyl Yermagambet
"""Serve the actual daemon with one separately built web view package for browser mount tests."""

import hashlib
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from baqylau_extension_api.manifest.metadata import PackageAsset
from baqylau_extension_api.manifest.views import WebSlot, WebView

from api.runtime import ApplicationConfig, DashboardApplication
from tests.extension_api import manifest_samples
from tests.extension_host import package_fixture
from tests.extension_web.management_server import report_endpoint

OWNER = "test.view"
STYLE_PATH = "web/view.css"
# Every paragraph turns red inside the package's shadow tree; host paragraphs must not.
STYLE_SOURCE = b"p { color: rgb(255, 0, 0); }\n"
VIEW_SOURCE = b"""export function mount(target, context) {
  const line = target.ownerDocument.createElement('p');
  line.textContent = `Hello from ${context.extensionId} in ${context.scope.kind}`;
  target.append(line);
  return { update() {}, dispose() { line.remove(); } };
}
"""


def installation_view(slot: WebSlot) -> WebView:
    """Place the greeting module in one installation-wide slot.

    Returns:
        The view for that slot.

    """
    return WebView(
        view_id=f"{OWNER}.{slot}", title=slot.title(), slot=slot, scopes=("installation",),
        module=manifest_samples.MODULE_PATH,
    )


def write_view_package(root: Path) -> None:
    """Write a web-only package whose module renders one greeting line on a page, the toolbar, and the status line."""
    directory = root / OWNER
    sample = manifest_samples.web_manifest(OWNER)
    page = sample.contributions.web[0].model_copy(update={"styles": (STYLE_PATH,)})
    views = (page, installation_view("toolbar"), installation_view("status"))
    manifest = sample.model_copy(update={
        "assets": (
            PackageAsset(
                path=manifest_samples.MODULE_PATH, digest=hashlib.sha256(VIEW_SOURCE).hexdigest(),
                media_type="text/javascript",
            ),
            PackageAsset(path=STYLE_PATH, digest=hashlib.sha256(STYLE_SOURCE).hexdigest(), media_type="text/css"),
        ),
        "contributions": sample.contributions.model_copy(update={"web": views}),
    })
    directory.mkdir(parents=True)
    package_fixture.save_manifest(directory, manifest)
    package_fixture.write_file(directory, manifest_samples.MODULE_PATH, VIEW_SOURCE)
    package_fixture.write_file(directory, STYLE_PATH, STYLE_SOURCE)
    package_fixture.write_file(directory, manifest.e2e[0].path, b"# Package-owned discovery test entry.\n")


def main() -> int:
    """Run real discovery, lifecycle, and asset delivery without a user data directory.

    Returns:
        The daemon exit code after normal shutdown.

    """
    with TemporaryDirectory(prefix="baqylau-extension-view-") as temporary:
        directory = Path(temporary)
        # The browser runner names a package root so that a test can change a package while the host runs.
        packages = Path(os.environ.get("BAQYLAU_E2E_PACKAGES") or directory / "packages")
        write_view_package(packages)
        application = DashboardApplication(ApplicationConfig(
            data_directory=directory, extension_roots=(packages,),
            port=0, terminal="pty", notify_telegram=False, notify_webpush=False,
        ))
        return application.run(report_endpoint).exit_code


if __name__ == "__main__":
    sys.exit(main())

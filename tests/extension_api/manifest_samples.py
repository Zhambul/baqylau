# Copyright (c) 2026 Zhambyl Yermagambet
"""Build package declarations without importing any feature implementation."""

from baqylau_extension_api.manifest.contributions import Contributions
from baqylau_extension_api.manifest.metadata import BackendEntry, E2eCase, PackageAsset
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.manifest.views import WebView
from baqylau_extension_api.versions import API_VERSION

from tests.extension_api import samples

MODULE_PATH = "web/dist/extension.js"


def backend_manifest(owner: str = samples.EXTENSION_ID) -> ExtensionManifest:
    """Return a package whose factory does not need to be importable for discovery.

    Returns:
        A backend-only declaration with a worker E2E case.

    """
    return ExtensionManifest(
        extension_id=owner, name="Example extension", description="Test one external package.",
        package_version="1.0.0", api_requires=f"=={API_VERSION}", quality_policy="0.1.0a1",
        backend=BackendEntry(module="uninstalled_feature.backend"), capabilities=("lifecycle",),
        e2e=(E2eCase(case_id="worker", path="tests/e2e/test_worker.py", surfaces=("worker",)),),
    )


def web_manifest(owner: str = samples.EXTENSION_ID) -> ExtensionManifest:
    """Return a web-only package with no Python backend or capability.

    Returns:
        One independently built view and its immutable asset declaration.

    """
    return backend_manifest(owner).model_copy(update={
        "backend": None, "capabilities": (),
        "assets": (PackageAsset(path=MODULE_PATH, digest=samples.DIGEST, media_type="text/javascript"),),
        "contributions": Contributions(web=(WebView(
            view_id=f"{owner}.main", title="Example", slot="workspace_page",
            scopes=("workspace",), module=MODULE_PATH,
        ),)),
        "e2e": (E2eCase(case_id="web", path="tests/e2e/test_web.py", surfaces=("web",)),),
    })

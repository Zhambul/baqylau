# Copyright (c) 2026 Zhambyl Yermagambet
"""Serve the actual daemon with one web package that has schema settings, a secret, and a custom panel."""

import hashlib
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from baqylau_extension_api.manifest import metadata, settings, views
from baqylau_extension_api.models.documents import EncodedDocument, SchemaDefinition, SchemaRef

from api.runtime import ApplicationConfig, DashboardApplication
from tests.extension_api import manifest_samples
from tests.extension_host import package_fixture
from tests.extension_web.management_server import report_endpoint

OWNER = "test.settings"
PANEL_PATH = "web/panel.js"
SCHEMA_TEXT = (
    '{"type":"object","properties":{"label":{"type":"string","title":"Label"}},"required":["label"]}'
)
PANEL_SOURCE = b"""export function mount(target, context) {
  const button = target.ownerDocument.createElement('button');
  const line = target.ownerDocument.createElement('p');
  button.textContent = 'Save from panel';
  button.addEventListener('click', async () => {
    const current = await context.api.readSettings();
    const document = { schema_ref: current.effective.schema_ref, json_text: '{"label":"From panel"}' };
    await context.api.saveSettings(document, current.settingsRevision);
    line.textContent = 'Panel saved';
  });
  target.append(button, line);
  return { update() {}, dispose() { button.remove(); line.remove(); } };
}
"""


def settings_schema() -> SchemaDefinition:
    """Declare one object schema with a titled text field.

    Returns:
        The schema with the digest of its bytes.

    """
    digest = hashlib.sha256(SCHEMA_TEXT.encode()).hexdigest()
    reference = SchemaRef(owner=OWNER, name="settings", version=1, digest=digest)
    return SchemaDefinition(reference=reference, json_text=SCHEMA_TEXT)


def write_settings_package(root: Path) -> None:
    """Write a web-only package with settings, one secret reference, and a custom settings panel."""
    directory = root / OWNER
    sample = manifest_samples.web_manifest(OWNER)
    schema = settings_schema()
    panel = views.WebView(
        view_id=f"{OWNER}.panel", title="Panel", slot="settings", scopes=("installation",), module=PANEL_PATH,
    )
    manifest = sample.model_copy(update={
        "assets": (metadata.PackageAsset(
            path=PANEL_PATH, digest=hashlib.sha256(PANEL_SOURCE).hexdigest(), media_type="text/javascript",
        ),),
        "schemas": (schema,),
        "settings": settings.SettingsDefinition(
            defaults=EncodedDocument(schema_ref=schema.reference, json_text='{"label":"Default"}'),
            scopes=("installation", "workspace"),
            secret_references=(settings.SecretSetting(name="token"),),
        ),
        "contributions": sample.contributions.model_copy(update={"web": (panel,)}),
    })
    directory.mkdir(parents=True)
    package_fixture.save_manifest(directory, manifest)
    package_fixture.write_file(directory, PANEL_PATH, PANEL_SOURCE)
    package_fixture.write_file(directory, manifest.e2e[0].path, b"# Package-owned discovery test entry.\n")


def main() -> int:
    """Run real discovery, lifecycle, settings, and secret storage without a user data directory.

    Returns:
        The daemon exit code after normal shutdown.

    """
    # The browser runner starts this process outside pytest; secrets must never reach the user's
    # keychain. The keyring library selects its backend on first use, after this line.
    os.environ["PYTHON_KEYRING_BACKEND"] = "tests.memory_keyring.MemoryKeyring"
    with TemporaryDirectory(prefix="baqylau-extension-settings-") as temporary:
        directory = Path(temporary)
        write_settings_package(directory / "packages")
        application = DashboardApplication(ApplicationConfig(
            data_directory=directory, extension_roots=(directory / "packages",),
            port=0, terminal="pty", notify_telegram=False, notify_webpush=False,
        ))
        return application.run(report_endpoint).exit_code


if __name__ == "__main__":
    sys.exit(main())

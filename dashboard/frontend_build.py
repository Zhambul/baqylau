"""Validate and describe the browser bundle that FastAPI serves."""
from __future__ import annotations

import hashlib
import html
import json
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath

from dashboard.config import STATIC_DIR

FRONTEND_DIRECTORY = Path(STATIC_DIR).parent / "frontend"
BUILD_DIRECTORY = Path(STATIC_DIR) / "build"
MANIFEST_PATH = BUILD_DIRECTORY / ".vite" / "manifest.json"
STAMP_PATH = BUILD_DIRECTORY / ".source-sha256"
ENTRY_MODULE = "src/main.ts"

type JsonValue = (
    str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
)

_CONFIGURATION_FILES = (
    "package-lock.json",
    "package.json",
    "svelte.config.js",
    "tsconfig.json",
    "tsconfig.node.json",
    "vite.config.ts",
)


class FrontendBuildError(RuntimeError):
    """The generated frontend is missing, invalid, or stale."""


def _source_files() -> tuple[Path, ...]:
    files = [FRONTEND_DIRECTORY / name for name in _CONFIGURATION_FILES]
    source_directory = FRONTEND_DIRECTORY / "src"
    files.extend(
        path
        for path in source_directory.rglob("*")
        if path.is_file()
        and not path.name.endswith(".test.ts")
        and "test" not in path.relative_to(source_directory).parts
    )
    files.append(Path(STATIC_DIR) / "style.css")
    return tuple(sorted(files))


def source_digest() -> str:
    """Return one stable digest for every production frontend input."""
    digest = hashlib.sha256()
    repository_root = FRONTEND_DIRECTORY.parent.parent
    for path in _source_files():
        try:
            relative = path.relative_to(repository_root).as_posix().encode()
            data = path.read_bytes()
        except OSError as error:
            raise FrontendBuildError("frontend source is unreadable: %s" % path) from error
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def write_build_stamp() -> None:
    """Record the inputs that produced the current Vite bundle."""
    if not MANIFEST_PATH.is_file():
        raise FrontendBuildError("frontend manifest is missing after the build")
    STAMP_PATH.write_text(source_digest() + "\n", encoding="ascii")


def validate_frontend_build() -> None:
    """Fail loudly when the daemon would serve missing or stale bytes."""
    if not MANIFEST_PATH.is_file() or not STAMP_PATH.is_file():
        raise FrontendBuildError("frontend build is missing; run `make build-frontend`")
    try:
        stamped = STAMP_PATH.read_text(encoding="ascii").strip()
    except OSError as error:
        raise FrontendBuildError("frontend build stamp is unreadable") from error
    if stamped != source_digest():
        raise FrontendBuildError("frontend build is stale; run `make build-frontend`")
    read_manifest()


def _mapping(value: JsonValue, context: str) -> Mapping[str, JsonValue]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise FrontendBuildError("%s must be an object with string keys" % context)
    return value


def _strings(value: JsonValue, context: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise FrontendBuildError("%s must be a string list" % context)
    strings: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise FrontendBuildError("%s must be a string list" % context)
        strings.append(item)
    return tuple(strings)


def _asset_name(value: JsonValue, context: str) -> str:
    if not isinstance(value, str):
        raise FrontendBuildError("%s must be a string" % context)
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "assets":
        raise FrontendBuildError("%s is not a safe build asset" % context)
    return value


def read_manifest() -> Mapping[str, Mapping[str, JsonValue]]:
    """Read the Vite manifest through a small, checked boundary."""
    try:
        raw: JsonValue = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise FrontendBuildError("frontend manifest is unreadable") from error
    manifest = _mapping(raw, "frontend manifest")
    checked: dict[str, Mapping[str, JsonValue]] = {}
    for key, value in manifest.items():
        checked[key] = _mapping(value, "frontend manifest entry %s" % key)
    if ENTRY_MODULE not in checked:
        raise FrontendBuildError("frontend manifest has no %s entry" % ENTRY_MODULE)
    return checked


def _entry_assets(
    manifest: Mapping[str, Mapping[str, JsonValue]],
    key: str,
    visited: set[str],
) -> tuple[list[str], list[str]]:
    if key in visited:
        return [], []
    visited.add(key)
    try:
        entry = manifest[key]
    except KeyError as error:
        raise FrontendBuildError("frontend manifest import is missing: %s" % key) from error

    styles: list[str] = []
    modules: list[str] = []
    css = entry.get("css", [])
    for index, name in enumerate(_strings(css, "%s.css" % key)):
        styles.append(_asset_name(name, "%s.css[%d]" % (key, index)))
    imports = entry.get("imports", [])
    for imported in _strings(imports, "%s.imports" % key):
        imported_styles, imported_modules = _entry_assets(manifest, imported, visited)
        styles.extend(imported_styles)
        modules.extend(imported_modules)
        modules.append(_asset_name(manifest[imported].get("file"), "%s.file" % imported))
    return styles, modules


def manifest_tags() -> bytes:
    """Render the CSS, preload, and entry tags for the FastAPI-owned shell."""
    manifest = read_manifest()
    entry = manifest[ENTRY_MODULE]
    styles, modules = _entry_assets(manifest, ENTRY_MODULE, set())
    entry_file = _asset_name(entry.get("file"), "%s.file" % ENTRY_MODULE)
    lines = [
        *(
            f'<link rel="stylesheet" href="/static/build/{html.escape(name, quote=True)}">'
            for name in dict.fromkeys(styles)
        ),
        *(
            '<link rel="modulepreload" crossorigin '
            f'href="/static/build/{html.escape(name, quote=True)}">'
            for name in dict.fromkeys(modules)
        ),
        f'<script type="module" crossorigin src="/static/build/{html.escape(entry_file, quote=True)}"></script>',
    ]
    return ("\n".join(lines) + "\n").encode()


def build_asset_path(asset_name: str) -> Path:
    """Resolve one manifest-shaped asset without admitting a user path."""
    safe_name = _asset_name(asset_name, "build asset")
    build_root = BUILD_DIRECTORY.resolve()
    path = build_root.joinpath(*PurePosixPath(safe_name).parts).resolve()
    try:
        path.relative_to(build_root)
    except ValueError as error:
        raise FrontendBuildError("build asset escapes its directory") from error
    return path


def main(arguments: Sequence[str] | None = None) -> int:
    """Write the build stamp after Vite completes."""
    if tuple(arguments or ()) != ("--stamp",):
        raise FrontendBuildError("usage: python -m dashboard.frontend_build --stamp")
    write_build_stamp()
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))

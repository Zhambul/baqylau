# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate the source inputs of the generated frontend."""

from __future__ import annotations

import hashlib
from pathlib import Path

from dashboard.config import STATIC_DIR

FRONTEND_DIRECTORY = Path(STATIC_DIR).parent / "frontend"
POLICY_DIRECTORY = FRONTEND_DIRECTORY.parent.parent / "packages" / "dev-tools-web"
BUILD_DIRECTORY = Path(STATIC_DIR) / "build"
MANIFEST_PATH = BUILD_DIRECTORY / ".vite" / "manifest.json"
STAMP_PATH = BUILD_DIRECTORY / ".source-sha256"
ENTRY_MODULE = "src/main.ts"

CONFIGURATION_FILES = (
    ".npmrc",
    "package-lock.json",
    "package.json",
    "svelte.config.js",
    "tsconfig.json",
    "tsconfig.node.json",
    "vite.config.ts",
)

POLICY_CONFIGURATION_FILES = (
    "package.json",
    "package-lock.json",
    "tsconfig.browser.json",
    "tsconfig.node.json",
    "coverage.mjs",
)


class FrontendBuildError(RuntimeError):
    """The generated frontend is missing, invalid, or stale."""


def source_files() -> tuple[Path, ...]:
    """Return every production frontend input.

    Returns:
        The frontend input files.

    """
    files = [FRONTEND_DIRECTORY / name for name in CONFIGURATION_FILES]
    files.extend(POLICY_DIRECTORY / name for name in POLICY_CONFIGURATION_FILES)
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
    """Return a stable digest for production frontend input.

    Returns:
        The source digest.

    Raises:
        FrontendBuildError: If a source file cannot be read.

    """
    digest = hashlib.sha256()
    for path in source_files():
        try:
            relative, source_content = source_input(path)
        except OSError as error:
            message = f"frontend source is unreadable: {path}"
            raise FrontendBuildError(message) from error
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(source_content).to_bytes(8, "big"))
        digest.update(source_content)
    return digest.hexdigest()


def source_input(path: Path) -> tuple[bytes, bytes]:
    """Return the stable name and content of one input.

    Returns:
        The relative file name and content.

    """
    return (
        path.relative_to(FRONTEND_DIRECTORY.parent.parent).as_posix().encode(),
        path.read_bytes(),
    )


def write_build_stamp() -> None:
    """Record the inputs of the current Vite bundle.

    Raises:
        FrontendBuildError: If the manifest is missing.

    """
    if not MANIFEST_PATH.is_file():
        message = "frontend manifest is missing after the build"
        raise FrontendBuildError(message)
    STAMP_PATH.write_text(f"{source_digest()}\n", encoding="ascii")

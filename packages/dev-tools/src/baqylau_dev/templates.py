# Copyright (c) 2026 Zhambyl Yermagambet
"""Write a new extension package from the shared template.

The package has a backend with a lifecycle and a greeting query, an optional
web view and terminal view, its quality profile, thin Make wrappers around the
installed tools, a unit test, and a kit E2E case. The manifest is built with
the SDK's own models, so its schema digests match.
"""

from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from string import Template

from baqylau_extension_api.versions import API_VERSION

from baqylau_dev import template_parts as parts
from baqylau_dev.resources import policy_version
from baqylau_dev.template_manifest import package_manifest, text_digest

ENCODING = "utf-8"
HEADER = "Copyright (c) 2026 Zhambyl Yermagambet"


@dataclass(frozen=True)
class PackageChoice:
    """Name the new package and the optional parts that it has."""

    extension_id: str
    web: bool = False
    terminal: bool = False

    @property
    def module(self) -> str:
        """The Python package name of the backend."""
        return self.extension_id.replace(".", "_").replace("-", "_")


def create_package(directory: Path, choice: PackageChoice) -> None:
    """Write the package into an empty or new directory.

    Raises:
        ValueError: If the directory has files.

    """
    if directory.exists() and any(directory.iterdir()):
        message = f"{directory} is not empty"
        raise ValueError(message)
    substitutions = _substitutions(choice)
    for source, target in _files(choice):
        _write(directory / target, Template(_template(source)).substitute(substitutions))
    manifest = package_manifest(directory, choice)
    (directory / "extension.json").write_text(
        f"{manifest.model_dump_json(indent=2, exclude_defaults=True)}\n", encoding=ENCODING,
    )


def _files(choice: PackageChoice) -> tuple[tuple[str, str], ...]:
    backend = f"src/{choice.module}"
    common = (
        ("pyproject.toml.in", "pyproject.toml"), ("baqylau-dev.toml.in", "baqylau-dev.toml"),
        ("Makefile.in", "Makefile"), ("gitignore.in", ".gitignore"),
        ("src/backend.py.in", f"{backend}/backend.py"), ("package.in", f"{backend}/__init__.py"),
        ("tests/test_backend.py.in", "tests/test_backend.py"), ("package.in", "tests/__init__.py"),
        ("tests/e2e/test_package.py.in", "tests/e2e/test_package.py"), ("package.in", "tests/e2e/__init__.py"),
    )
    web = (
        ("web/view.js.in", parts.WEB_MODULE), ("package.json.in", "package.json"),
        ("tsconfig.json.in", "tsconfig.json"), ("eslint.config.js.in", "eslint.config.js"),
        ("prettierignore.in", ".prettierignore"),
    )
    return (*common, *web) if choice.web else common


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding=ENCODING)


def _substitutions(choice: PackageChoice) -> dict[str, str]:
    return {
        "header": HEADER, "extension_id": choice.extension_id, "module": choice.module,
        "distribution": choice.extension_id.replace(".", "-"), "sdk_version": API_VERSION,
        "policy_version": policy_version(), "text_digest": text_digest(),
        **parts.terminal_values(terminal=choice.terminal),
        **parts.e2e_values(web=choice.web, terminal=choice.terminal),
        **parts.web_values(web=choice.web),
    }


def _template(name: str) -> str:
    return resources.files("baqylau_dev").joinpath("template", name).read_text(encoding=ENCODING)

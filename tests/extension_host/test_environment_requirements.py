# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep runtime locks within the captured package and standard pinned wheel syntax."""

from pathlib import Path

import pytest

from extensions.environment_requirements import MAX_REQUIREMENTS_BYTES, validate_runtime_requirements

SHA256_LENGTH = 64
DIGEST = "a" * SHA256_LENGTH
HASH = f" --hash=sha256:{DIGEST}"
LOCK_NAME = "requirements.lock"
COMPILED_LOCK = Path(__file__).with_name("fixtures") / "runtime-requirements.txt"
INVALID_REQUIREMENTS = (
    "example", "example>=1", "example==1.*", "example===1", "example==1,!=2",
    "example @ https://example.invalid/source.whl", "example @ file:///tmp/source.whl",
    "-r /tmp/requirements.txt", "-c /tmp/constraints.txt", "-e ./source", "../source",
    "--find-links /tmp/wheels", "--index-url https://example.invalid/simple",
)


@pytest.mark.parametrize("requirement", INVALID_REQUIREMENTS)
def test_lock_rejects_unpinned_or_external_input(tmp_path: Path, requirement: str) -> None:
    """Installer directives and direct source references cannot leave captured storage."""
    path = tmp_path / LOCK_NAME
    path.write_text(f"{requirement}{HASH}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="runtime requirements"):
        validate_runtime_requirements(path)


@pytest.mark.parametrize("hashes", ["", " --hash=sha256:short", " --hash=md5:abc"])
def test_lock_requires_full_sha256(tmp_path: Path, hashes: str) -> None:
    """Every selected requirement needs a complete SHA-256 digest."""
    path = tmp_path / LOCK_NAME
    path.write_text(f"example==1{hashes}", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256"):
        validate_runtime_requirements(path)


def test_lock_accepts_standard_compile_output(tmp_path: Path) -> None:
    """Comments, continuations, extras, markers, and multiple wheel hashes are valid."""
    path = tmp_path / LOCK_NAME
    path.write_bytes(COMPILED_LOCK.read_bytes())
    validate_runtime_requirements(path)


def test_lock_has_a_file_size_bound(tmp_path: Path) -> None:
    """A large dependency document fails before parsing or subprocess launch."""
    path = tmp_path / LOCK_NAME
    path.write_bytes(b"#" * (MAX_REQUIREMENTS_BYTES + 1))
    with pytest.raises(ValueError, match="limit"):
        validate_runtime_requirements(path)

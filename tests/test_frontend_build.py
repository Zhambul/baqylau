"""The production frontend build contract."""
from pathlib import Path

import pytest

from dashboard import frontend_build


def test_current_bundle_matches_its_sources():
    frontend_build.validate_frontend_build()


def test_manifest_tags_name_only_checked_hashed_assets():
    tags = frontend_build.manifest_tags().decode()

    assert 'rel="stylesheet"' in tags
    assert '<script type="module" crossorigin' in tags
    assert "/static/build/assets/" in tags
    assert "../" not in tags


def test_build_asset_path_rejects_traversal():
    with pytest.raises(frontend_build.FrontendBuildError):
        frontend_build.build_asset_path("../index.html")


def test_build_asset_path_rejects_a_symlink_escape(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    build = tmp_path / "build"
    assets = build / "assets"
    assets.mkdir(parents=True)
    outside = tmp_path / "outside.js"
    outside.write_text("unsafe", encoding="utf-8")
    (assets / "linked.js").symlink_to(outside)
    monkeypatch.setattr(frontend_build, "BUILD_DIRECTORY", build)

    with pytest.raises(frontend_build.FrontendBuildError, match="escapes"):
        frontend_build.build_asset_path("assets/linked.js")


def test_stale_source_digest_stops_startup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    stamp = tmp_path / "stamp"
    stamp.write_text("not-the-current-digest\n", encoding="ascii")
    monkeypatch.setattr(frontend_build, "STAMP_PATH", stamp)

    with pytest.raises(frontend_build.FrontendBuildError, match="stale"):
        frontend_build.validate_frontend_build()

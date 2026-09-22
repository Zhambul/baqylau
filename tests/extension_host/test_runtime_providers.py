# Copyright (c) 2026 Zhambyl Yermagambet
"""Resolve resource ownership from the actual application database and daemon mode."""

from contextlib import closing
from http import HTTPStatus
from pathlib import Path

import pytest
from fastapi import FastAPI

from app import injection, provider_databases, provider_extension_runtime, provider_extensions
from extensions.runtime_ownership import FilesystemRuntimeOwnership
from extensions.runtime_ownership_contract import RuntimeBusyError
from repository.impl.sqlite import databases
from tests.extension_host import catalog_fixture


def test_factory_uses_selected_database_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A private provider graph does not acquire the normal application's lock."""
    monkeypatch.setenv("BAQYLAU_DATA_DIR", str(tmp_path / "not-selected"))
    instances = injection.registry()
    database = databases.main_database(str(tmp_path / "selected" / "main.db"))
    injection.seed(instances, provider_databases.main_db, database)
    factory = injection.resolve(instances, provider_extension_runtime.extension_manager_factory)
    with closing(factory.open_manager()), pytest.raises(RuntimeBusyError):
        FilesystemRuntimeOwnership(tmp_path / "selected").acquire_runtime()
    assert not (tmp_path / "not-selected").exists()


def test_request_only_lifespan_has_no_manager(tmp_path: Path) -> None:
    """Catalog HTTP tests and schema reads do not claim or restore runtime state."""
    with catalog_fixture.web_client(tmp_path) as client:
        assert isinstance(client.app, FastAPI)
        state = injection.resolve(client.app.state.instances, provider_extension_runtime.extension_runtime)
        assert state.manager is None
        with closing(FilesystemRuntimeOwnership(tmp_path).acquire_runtime()):
            assert client.get("/api/extensions").status_code == HTTPStatus.OK
    assert not (tmp_path / "extension-environments").exists()


def test_default_roots_use_selected_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit roots remain configurable; the default belongs to this application."""
    monkeypatch.delenv("BAQYLAU_EXTENSION_ROOTS", raising=False)
    instances = injection.registry()
    database = databases.main_database(str(tmp_path / "main.db"))
    injection.seed(instances, provider_databases.main_db, database)
    selected = injection.resolve(instances, provider_extensions.extension_roots)
    assert selected.directories == (tmp_path / "extensions",)

# Copyright (c) 2026 Zhambyl Yermagambet
"""Check installed imports and reject incorrect external type signatures."""

import subprocess  # noqa: S404 -- Run fixed Python commands for package boundary tests.
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
IMPORT_TIMEOUT_SECONDS = 30


def test_sdk_imports_without_host_on_search_path(tmp_path: Path) -> None:
    """Import the installed SDK with cwd and Python path isolation."""
    program = (
        "from importlib.util import find_spec\n"
        "from baqylau_extension_api.contracts.plugin import ExtensionPlugin\n"
        "from baqylau_extension_api.schemas import SchemaSet\n"
        "from baqylau_extension_api.models.raw_transforms import RawTransformResult\n"
        "assert find_spec('app') is None\n"
        "assert find_spec('domain') is None\n"
        "assert RawTransformResult().operations == ()\n"
    )
    result = subprocess.run(  # noqa: S603 -- Run a fixed program with the current Python, without a shell.
        [sys.executable, "-I", "-c", program], cwd=tmp_path,
        capture_output=True, text=True, check=False, timeout=IMPORT_TIMEOUT_SECONDS,
    )
    assert result.returncode == 0, result.stderr


def test_example_imports_without_host(tmp_path: Path) -> None:
    """Load an external backend fixture with only the installed public SDK."""
    example = Path(__file__).with_name("example.py")
    result = subprocess.run(  # noqa: S603 -- Load a checked-in fixture, without a shell.
        [sys.executable, "-I", str(example)], cwd=tmp_path,
        capture_output=True, text=True, check=False, timeout=IMPORT_TIMEOUT_SECONDS,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(("contract_module", "protocol_name", "method_name", "request_type"), [
    ("processing", "ExtensionRawTransformer", "transform", "transforms.RawTransformRequest"),
    ("operations", "ExtensionQueries", "query", "queries.QueryRequest"),
    ("operations", "ExtensionCommands", "execute", "commands.CommandRequest"),
    ("sources", "ExtensionSources", "read", "sources.SourceReadRequest"),
    ("sources", "ExtensionTranslator", "translate", "translation_inputs.ExtensionTranslationRequest"),
    ("projection", "ExtensionProjector", "project", "projections.ProjectionRequest"),
    ("projection", "ExtensionProjector", "select_records", "projections.ProjectionSelectionRequest"),
    ("projection", "ExtensionProjectionTransformer", "transform", "projection_transforms.ProjectionTransformRequest"),
    ("migrations", "ExtensionMigrations", "migrate_settings", "migrations.SettingsMigrationRequest"),
    ("migrations", "ExtensionMigrations", "migrate_records", "migrations.RecordMigrationRequest"),
    ("observers", "ExtensionObserver", "observe", "observer_jobs.ObservationJobRequest"),
    ("observers", "ExtensionObserver", "cancel_observation", "observer_jobs.ObservationCancelRequest"),
    ("observers", "ExtensionObserver", "reconcile_observation", "observer_jobs.ObservationReconcileRequest"),
    ("service_access", "ExtensionServiceAccess", "resolve_service", "services.ServiceResolveRequest"),
    ("service_access", "ExtensionServiceAccess", "query_service", "services.ServiceQueryRequest"),
])
def test_mypy_rejects_incorrect_capability(
    tmp_path: Path, contract_module: str, protocol_name: str, method_name: str, request_type: str,
) -> None:
    """Reject incorrect external processing and live-operation response types."""
    source = tmp_path / "incorrect.py"
    source.write_text(
        f"from baqylau_extension_api.contracts.{contract_module} import {protocol_name}\n"
        "from baqylau_extension_api.models import transforms, queries, commands, sources, translation_inputs\n"
        "from baqylau_extension_api.models import projections\n"
        "from baqylau_extension_api.models import projection_transforms, migrations, observer_jobs, services\n"
        f"class Incorrect({protocol_name}):\n"
        f"    def {method_name}(self, request: {request_type}) -> str:\n"
        "        return 'invalid'\n",
        encoding="utf-8",
    )
    result = subprocess.run(  # noqa: S603 -- Check fixed source with the installed type checker, without a shell.
        [sys.executable, "-m", "mypy", "--strict", "--config-file", str(ROOT / "mypy.ini"),
         "--cache-dir", str(tmp_path / "cache"), str(source)],
        cwd=tmp_path, capture_output=True, text=True, check=False, timeout=60,
    )
    assert result.returncode == 1, result.stderr
    assert "[override]" in result.stdout and 'Return type "str"' in result.stdout

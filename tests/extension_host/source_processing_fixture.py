# Copyright (c) 2026 Zhambyl Yermagambet
"""Connect local source probes to real storage and the actual registry read boundary."""

from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from unittest.mock import Mock

from baqylau_extension_api.contracts.services import ExtensionHostServices
from baqylau_extension_api.models import directory, environment
from baqylau_extension_api.runtime.call_grants import HostCallLedger

from audit.failures import CoalescingFailureRecorder, ErrorRecorder
from extensions import (
    manager_contract,
    processing_runtime,
    registry_package,
    registry_services,
    registry_snapshot,
    source_processing_contract,
    source_resources,
    source_scopes,
)
from extensions.models.manager import ManagerSnapshot
from tests.extension_api import source_example
from tests.extension_host import catalog_fixture, registry_memory_fixture, source_probe, source_read_fixture


@dataclass
class SourceClock:
    """Advance deadlines without waiting on wall time in scheduler tests."""

    now: float = 1000.0

    def __call__(self) -> float:
        """Read the explicit fixture clock.

        Returns:
            The selected finite time.

        """
        return self.now


@dataclass
class SourceWatches(source_processing_contract.ExtensionSourceWatches):
    """Record subscription order; native delivery has separate tests."""

    trace: source_probe.SourceTrace
    paths: frozenset[Path] = field(default_factory=frozenset)

    def watch_sources(self, paths: frozenset[Path]) -> None:
        """Retain the complete selected set before the next read."""
        self.paths = paths
        self.trace.actions.append("watch")


@dataclass(frozen=True)
class SourceCase:
    """Keep unit source probes distinct from real database and registry behavior."""

    original: source_read_fixture.SourceCase
    runtime: processing_runtime.ProcessingRuntime
    probe: source_probe.SourceProbe
    clock: SourceClock
    watches: SourceWatches
    scopes: source_scopes.ActiveExtensionScopes
    manager: Mock

    def run(self, *, refresh: bool = True) -> float | None:
        """Complete one actual retained runtime pass.

        Returns:
            Its next source deadline, if any.

        """
        with self.runtime.capture_batch() as batch:
            assert batch is not None
            return batch.read_sources(self.watches, Event().is_set, refresh_plans=refresh)


def installed(directory_path: Path) -> SourceCase:
    """Retain a real declaration and runtime, with only the source and manager calls controlled.

    Returns:
        The production source runtime around local explicit source protocols.

    """
    original = source_read_fixture.installed(directory_path)
    probe = source_probe.SourceProbe()
    probe.plan = (probe.plan[0].model_copy(update={
        "watch_paths": (str(directory_path / "input.log"),),
    }),)
    snapshot = _snapshot(original, probe)
    registry = registry_memory_fixture.MemoryRegistry("initial")
    assert registry.publish_snapshot(0, snapshot).status == "accepted"
    return _case(original, probe, registry, snapshot)


def _snapshot(
    original: source_read_fixture.SourceCase, probe: source_probe.SourceProbe,
) -> registry_snapshot.RuntimeSnapshot:
    state = original.original.lifecycle.read_extension_lifecycle()
    assert state.committed_runtime is not None
    selected = state.committed_runtime.packages[0]
    catalog = catalog_fixture.repository(Path(original.store.database.path).parent).read_extension_catalog()
    assert catalog.entries[0].manifest is not None
    identity = environment.ExtensionEnvironment(
        extension_info=selected.extension_info, runtime_revision=state.committed_runtime.runtime_revision,
    )
    base = source_example.SourceExample(ExtensionHostServices(
        registry_services.RegistryDirectory(registry_memory_fixture.MemoryRegistry("unused")), identity,
    ))
    return registry_snapshot.prepare_snapshot(
        state.committed_runtime.catalog_revision, identity.runtime_revision, (registry_package.RegistryPackage(
            catalog.entries[0].manifest,
            directory.DirectoryEntry(extension_info=selected.extension_info, state="enabled"),
            identity, source_probe.SourcePlugin(base, probe), selected.settings,
        ),),
    )


def _case(
    original: source_read_fixture.SourceCase, probe: source_probe.SourceProbe,
    registry: registry_memory_fixture.MemoryRegistry, snapshot: registry_snapshot.RuntimeSnapshot,
) -> SourceCase:
    manager = Mock(spec=manager_contract.ExtensionManager, read_state=Mock(return_value=ManagerSnapshot(
        lifecycle=original.original.lifecycle.read_extension_lifecycle(), registry_revision=1,
        active_runtime=snapshot.directory.runtime_revision, directory=snapshot.directory, phase="running",
    )))
    callbacks = source_resources.SourceCallbacks(
        Mock(), CoalescingFailureRecorder(Mock(spec=ErrorRecorder), "source tests"), SourceClock(),
    )
    scopes = source_scopes.ActiveExtensionScopes(callbacks.changed)
    runtime = processing_runtime.ProcessingRuntime(source_resources.SourceServices(
        manager, registry, scopes, original.store, HostCallLedger(),
    ), callbacks, source_read_fixture.interpretation_stores(original))
    assert isinstance(callbacks.clock, SourceClock)
    return SourceCase(original, runtime, probe, callbacks.clock, SourceWatches(probe.trace), scopes, manager)

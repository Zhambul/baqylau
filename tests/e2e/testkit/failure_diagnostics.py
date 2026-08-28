"""Small state report for one failed live E2E scenario."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import psutil

from domain.ids import HarnessName
from sdk.client import BaqylauClient
from tests.e2e.testkit.process import ApplicationProcess

VISIBLE_ENVIRONMENT_NAMES = (
    "BAQYLAU_DASHBOARD_PORT",
    "CODEX_HOME",
    "CLAUDE_CONFIG_DIR",
    "CLAUDE_CODE_MANAGED_SETTINGS_PATH",
)
MAXIMUM_TEXT_CHARACTERS = 4_000


def save_e2e_failure_diagnostics(
    application: ApplicationProcess,
    node_id: str,
    diagnostics: str,
) -> Path:
    """Save the complete report beside this worker's isolated databases."""
    path = application.config.data_directory / "e2e-failure-report.txt"
    path.write_text(
        f"test={node_id}\n\n{diagnostics}\n",
        encoding="utf-8",
    )
    return path


def e2e_failure_diagnostics(
    application: ApplicationProcess,
    window_ids: frozenset[str] | None = None,
) -> str:
    """Describe the failed test process, its stored state, and its terminal."""
    sections = [
        _system_state(),
        _application_state(application),
        _terminal_state(application, window_ids),
        _profile_state(application),
        _database_state(application.config.data_directory),
    ]
    return "\n\n".join(section for section in sections if section)


def e2e_stall_diagnostics(
    application: ApplicationProcess,
    window_ids: frozenset[str] | None = None,
) -> str:
    """Return the small live-state part of the full failure report."""
    return "\n\n".join(
        (
            _system_state(),
            _application_state(application),
            _terminal_state(application, window_ids),
            _profile_state(application),
        )
    )


def e2e_progress_marker(application: ApplicationProcess) -> tuple[int, int, int]:
    """Return counters that change when a scenario makes stored progress."""
    database = application.config.data_directory / "main.db"
    if not database.exists():
        return (0, 0, 0)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=1)
        row = connection.execute(
            "SELECT (SELECT COUNT(*) FROM sessions), "
            "(SELECT COUNT(*) FROM raw_events), "
            "(SELECT COUNT(*) FROM canonical_events)"
        ).fetchone()
    except sqlite3.Error:
        return (0, 0, 0)
    finally:
        if connection is not None:
            connection.close()
    if row is None:
        return (0, 0, 0)
    return (int(row[0] or 0), int(row[1] or 0), int(row[2] or 0))


def _system_state() -> str:
    names = {"claude": 0, "codex": 0, "python": 0}
    e2e_daemons: list[tuple[int, str]] = []
    for process in psutil.process_iter(("name", "cmdline")):
        try:
            name = (process.info["name"] or "").lower()
            command = tuple(process.info["cmdline"] or ())
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        for key in names:
            if key in name:
                names[key] += 1
        if "python" not in name or not any("spawn_main" in item for item in command):
            continue
        try:
            parent = process.parent()
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        if parent is not None:
            e2e_daemons.append((process.pid, f"parent:{parent.pid}"))
    memory = psutil.virtual_memory()
    return (
        "system\n"
        f"  load={tuple(round(value, 2) for value in os.getloadavg())}\n"
        f"  available_memory_mb={memory.available // (1024 * 1024)}\n"
        f"  process_counts={names}\n"
        f"  e2e_daemons={e2e_daemons}"
    )


def _application_state(application: ApplicationProcess) -> str:
    process = application.process
    lines = [
        "application",
        f"  endpoint={application.endpoint.host}:{application.endpoint.port}",
        f"  pid={process.pid} alive={process.is_alive()} exit_code={process.exitcode}",
        f"  data_directory={application.config.data_directory}",
    ]
    if process.pid is None:
        return "\n".join(lines)
    try:
        root = psutil.Process(process.pid)
        related = (root, *root.children(recursive=True))
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess) as error:
        lines.append(f"  process_read_error={error}")
        return "\n".join(lines)
    now = time.time()
    for related_process in related:
        lines.append("  " + _process_line(related_process, now))
    return "\n".join(lines)


def _process_line(process: psutil.Process, now: float) -> str:
    try:
        memory_mb = process.memory_info().rss // (1024 * 1024)
        descriptors = process.num_fds() if hasattr(process, "num_fds") else -1
        return (
            f"process pid={process.pid} name={process.name()!r} status={process.status()} "
            f"parent={process.ppid()} "
            f"age_seconds={round(now - process.create_time(), 1)} "
            f"cpu_seconds={round(sum(process.cpu_times()), 2)} "
            f"threads={process.num_threads()} fds={descriptors} memory_mb={memory_mb} "
            f"command={_compact(process.cmdline())} "
            f"environment={_compact(_selected_environment(process.pid))}"
        )
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess) as error:
        return f"process pid={process.pid} read_error={error}"


def _database_state(directory: Path) -> str:
    sections = ["stored state"]
    sections.extend(_query_sections(directory / "main.db", (
        (
            "sessions",
            "SELECT session_id, harness, harness_session_id, terminal_window_id, "
            "harness_process_id, lifecycle, working_directory FROM sessions "
            "ORDER BY created_at DESC LIMIT 30",
        ),
        (
            "recent raw events",
            "SELECT id, session_id, harness, source_type, source_position, "
            "terminal_window_id, harness_process_id FROM raw_events "
            "ORDER BY id DESC LIMIT 40",
        ),
        (
            "pending raw events",
            "SELECT COUNT(*) AS count FROM pending_raw_events",
        ),
        (
            "pipeline cursors",
            "SELECT (SELECT MAX(cursor) FROM canonical_events) AS translated, "
            "(SELECT canonical_cursor FROM reaction_progress) AS reacted, "
            "(SELECT MAX(cursor) FROM session_entries) AS newest_entry",
        ),
        (
            "recent canonical events",
            "SELECT cursor, session_id, event_type, actor_id, turn_id, event_id, "
            "occurred_at, accepted_at, payload FROM canonical_events "
            "ORDER BY cursor DESC LIMIT 50",
        ),
        (
            "recent interpretation verdicts",
            "SELECT raw_event_id, decision, reason, completed_at FROM interpretations "
            "ORDER BY completed_at DESC LIMIT 50",
        ),
        (
            "recent session data",
            "SELECT session_id, revision, payload FROM session_data "
            "ORDER BY revision DESC LIMIT 30",
        ),
        (
            "new-session drafts",
            "SELECT working_directory, text, sequence FROM new_session_drafts "
            "ORDER BY sequence DESC LIMIT 30",
        ),
    )))
    sections.extend(_query_sections(directory / "audit.db", (
        (
            "recent control and state records",
            "SELECT id, ts, session_id, action, content, pid FROM state_files "
            "ORDER BY id DESC LIMIT 30",
        ),
        (
            "recent errors",
            "SELECT id, ts, session_id, script, func, context, pid FROM errors "
            "ORDER BY id DESC LIMIT 30",
        ),
        (
            "recent spawns",
            "SELECT id, ts, session_id, child_pid, purpose, argv FROM spawns "
            "ORDER BY id DESC LIMIT 30",
        ),
    )))
    return "\n".join(sections)


def _query_sections(
    database: Path,
    queries: Iterable[tuple[str, str]],
) -> list[str]:
    if not database.exists():
        return [f"  {database.name}: missing"]
    result: list[str] = []
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=1)
        connection.row_factory = sqlite3.Row
    except sqlite3.Error as error:
        return [f"  {database.name}: open_error={error}"]
    try:
        for label, query in queries:
            try:
                rows = [dict(row) for row in connection.execute(query)]
            except sqlite3.Error as error:
                result.append(f"  {label}: query_error={error}")
                continue
            result.append(f"  {label}: {_compact(rows)}")
    finally:
        connection.close()
    return result


def _terminal_state(
    application: ApplicationProcess,
    window_ids: frozenset[str] | None,
) -> str:
    lines = ["terminal"]
    client = BaqylauClient(application.endpoint.url)
    try:
        windows = client.diagnostics.terminal().windows
    except Exception as error:
        return f"terminal\n  read_error={type(error).__name__}: {error}"
    finally:
        client.close()
    if window_ids is not None:
        windows = tuple(
            window for window in windows if str(window.window_id) in window_ids
        )
    if not windows:
        return "terminal\n  windows=[]"
    for window in windows:
        process_values = []
        for process in window.processes:
            environment = (
                _selected_environment(process.process_id)
                if process.process_id is not None
                else {}
            )
            process_values.append(
                {
                    "process_id": process.process_id,
                    "command": process.command,
                    "environment": environment,
                }
            )
        lines.append(
            f"  window={window.window_id} processes={_compact(process_values)}"
        )
        lines.append(
            "  screen=" + _compact(window.screen or window.screen_error or "")
        )
    return "\n".join(lines)


def _profile_state(application: ApplicationProcess) -> str:
    lines = ["harness profiles"]
    for harness, runtime in application.config.harness_runtime_configs.entries():
        line = (
            f"  harness={harness} executable={runtime.executable!r} "
            f"configuration_directory={str(runtime.configuration_directory)!r} "
            f"settings_file={str(runtime.settings_file) if runtime.settings_file else None!r}"
        )
        lines.append(line)
        if harness != HarnessName.CLAUDE_CODE:
            continue
        profile = runtime.configuration_directory / ".claude.json"
        try:
            document = json.loads(profile.read_text(encoding="utf-8"))
            summary = {
                "exists": True,
                "hasCompletedOnboarding": document.get("hasCompletedOnboarding"),
                "lastOnboardingVersion": document.get("lastOnboardingVersion"),
                "theme": document.get("theme"),
            }
        except (OSError, ValueError) as error:
            summary = {"exists": profile.exists(), "read_error": str(error)}
        lines.append(f"  profile_state={_compact(summary)}")
    return "\n".join(lines)


def _selected_environment(process_id: int) -> dict[str, str]:
    try:
        environment = psutil.Process(process_id).environ()
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
        return {}
    return {
        name: environment[name]
        for name in VISIBLE_ENVIRONMENT_NAMES
        if name in environment
    }


def _compact(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False)
    text = text.replace("\x00", "")
    if len(text) <= MAXIMUM_TEXT_CHARACTERS:
        return text
    return text[:MAXIMUM_TEXT_CHARACTERS] + "…"

"""baqylau-dashboard.py [serve|start|stop|status|open] [options]

The web dashboard's CLI lifecycle. The implementation behind the thin
bin/baqylau-dashboard.py entry (that filename is operational audit
vocabulary — the spawn below re-launches it by name). Lives in the package so
it is importable/testable in-process, like the rest of the dashboard tier.

  serve   — run the server in the foreground (what `start` spawns; also the
            debugging mode: crashes are visible instead of DEVNULL'd)
  start   — spawn the server detached (core/spawn.spawn_detached — audited,
            start_new_session) unless one is already running; prints the URL
  stop    — SIGTERM whoever answers on the port
  status  — the answering pid + URL
  open    — start (if needed) and open the browser        [the default]
  rebuild — re-derive the read model from the canonical log (see rebuild())

Three launch arguments, and they are the SAME three things every environment
that runs more than one daemon has to say: which port, which data directory,
where the output goes. They front the environment variables that have always
carried those answers rather than replacing them — a flag wins, the variable is
the default, and nothing that worked before stops working.

  --port N        the port to bind (or to ask, for stop/status)
  --data-dir DIR  the whole data directory: main.db, audit.db, uploads
  --log FILE      send this daemon's own output there (serve, and start's child)
  --harness-executable HARNESS=FILE
  --harness-config-dir HARNESS=DIR
  --harness-settings-file HARNESS=FILE

Every command takes the first two, not just the launching ones: `stop` and
`status` find the daemon BY ITS PORT, so a second daemon on a second port is
addressable the same way it was started. `start` forwards whatever it was given
to the `serve` it spawns, so the child's command line reads like the one a person
would have typed.

Import-pure: no argv/I/O/DB/frontend work at import — everything runs inside a
function (docs/architecture.md import-time purity rule).
"""
import http.client
import os
import signal
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from audit import record
from audit.models import PathAudit
from core.process import process_is_alive

if TYPE_CHECKING:
    from harness.runtime import HarnessRuntimeConfigs

HEALTH_PATH = "/api/health"
HEALTH_TIMEOUT_SECONDS = 1.0


# A flag, and the variable it fronts. The mechanism is deliberately this small:
# set the variable, then let every module that already reads it answer as it
# always did — the port contract (`core/daemon/contract.py`), the data directory
# (`core/data.py`), and everything they feed. The alternative was threading three
# values through the server, the store, the paths and the audit floor, which is a
# lot of parameters to express "this process is configured differently".
LAUNCH_VARIABLES = {
    "--port": "BAQYLAU_DASHBOARD_PORT",
    "--data-dir": "BAQYLAU_DATA_DIR",
}
LOG_FLAG = "--log"
HARNESS_FLAGS = (
    "--harness-executable",
    "--harness-config-dir",
    "--harness-settings-file",
)


class UsageError(Exception):
    """Bad argv — the one failure this CLI reports rather than absorbs."""


class HealthProcess(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    process_id: int


@dataclass(frozen=True)
class _HarnessFlag:
    name: str
    value: str


@dataclass(frozen=True)
class _DashboardOptions:
    variables: Mapping[str, str]
    log_path: str | None
    harness_runtime_configs: "HarnessRuntimeConfigs"
    harness_flags: tuple[_HarnessFlag, ...]


def _options(arguments: list[str]) -> _DashboardOptions:
    """The launch flags, as (variables to set, log path).

    Accepts `--flag value` and `--flag=value`, because a person types the first
    and a script generates the second.
    """
    variables: dict[str, str] = {}
    log_path: str | None = None
    harness_flags: list[_HarnessFlag] = []
    remaining = list(arguments)
    while remaining:
        argument = remaining.pop(0)
        name, _, inline = argument.partition("=")
        if (
            name not in LAUNCH_VARIABLES
            and name != LOG_FLAG
            and name not in HARNESS_FLAGS
        ):
            raise UsageError("unknown option: %s" % argument)
        value = inline if inline else (remaining.pop(0) if remaining else "")
        if not value:
            raise UsageError("%s needs a value" % name)
        if name == LOG_FLAG:
            log_path = os.path.abspath(os.path.expanduser(value))
            continue
        if name in HARNESS_FLAGS:
            harness_name, separator, harness_value = value.partition("=")
            if not separator or not harness_name or not harness_value:
                raise UsageError(f"{name} needs HARNESS=VALUE")
            harness_flags.append(
                _HarnessFlag(
                    name,
                    f"{harness_name}="
                    f"{os.path.abspath(os.path.expanduser(harness_value))}",
                )
            )
            continue
        if name == "--port" and not value.isdigit():
            raise UsageError("--port needs a number, not %r" % value)
        variables[LAUNCH_VARIABLES[name]] = (
            os.path.abspath(os.path.expanduser(value)) if name == "--data-dir" else value
        )
    from dataclasses import replace  # noqa: PLC0415

    from domain.ids import HarnessName  # noqa: PLC0415
    from harness.runtime import default_harness_runtime_configs  # noqa: PLC0415

    configs = default_harness_runtime_configs()
    for flag in harness_flags:
        harness_value, configured_value = flag.value.split("=", 1)
        try:
            harness = HarnessName(harness_value)
        except ValueError as error:
            raise UsageError(f"unknown harness: {harness_value}") from error
        runtime = configs.for_harness(harness)
        if flag.name == "--harness-executable":
            runtime = replace(runtime, executable=configured_value)
        elif flag.name == "--harness-config-dir":
            configuration_directory = Path(configured_value)
            runtime = replace(
                runtime,
                configuration_directory=configuration_directory,
                use_vendor_default_configuration=(
                    runtime.use_vendor_default_configuration
                    and configuration_directory == runtime.configuration_directory
                ),
            )
        elif flag.name == "--harness-settings-file":
            runtime = replace(runtime, settings_file=Path(configured_value))
        configs = configs.updated(harness, runtime)
    return _DashboardOptions(
        variables,
        log_path,
        configs,
        tuple(harness_flags),
    )


def _forwarded(arguments: list[str]) -> list[str]:
    """The same flags, as a child's command line: what `start` hands `serve`."""
    options = _options(arguments)
    flags = []
    for flag, variable in LAUNCH_VARIABLES.items():
        if variable in options.variables:
            flags.extend([flag, options.variables[variable]])
    if options.log_path is not None:
        flags.extend([LOG_FLAG, options.log_path])
    for harness_flag in options.harness_flags:
        flags.extend([harness_flag.name, harness_flag.value])
    return flags


def _redirect(log_path: str) -> None:
    """Send this process's own output to a file, descriptors and all.

    `dup2` rather than reassigning `sys.stdout`, because the interesting output
    is not ours: uvicorn writes to the descriptor, and so does anything it
    imports. A caller redirecting with a shell got both; so must this.
    """
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    handle = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.dup2(handle, 1)
        os.dup2(handle, 2)
    finally:
        os.close(handle)


def _serve(harness_runtime_configs: "HarnessRuntimeConfigs") -> int:
    from dashboard.frontend_build import (  # noqa: PLC0415 — startup validation stays lazy
        FrontendBuildError,
        validate_frontend_build,
    )

    try:
        validate_frontend_build()
    except FrontendBuildError as error:
        print("dashboard cannot start: %s" % error, file=sys.stderr)
        return 1
    from api.runtime import ApplicationConfig, DashboardApplication  # noqa: PLC0415 — configuration precedes imports

    return DashboardApplication(
        ApplicationConfig.from_environment(harness_runtime_configs)
    ).run().exit_code


def holder() -> int:
    """The running server's pid, or 0.

    Asked over the port the daemon binds, because that bind IS the singleton
    guard — a pid claim in a database was a second answer to the same question,
    and it could disagree.

    Anything other than a pid falls through to `_listening_pid`, which asks the
    kernel who holds the port. That covers the two cases the probe cannot: a
    daemon wedged past answering, and — measured, the first time this shipped —
    a daemon still running the code from BEFORE /api/health existed, which
    answers the probe with a 404. Both are exactly the daemon you need `stop`
    for, and reporting "not running" at one is how you end up with two.
    """
    from core.daemon import contract as daemon_contract  # noqa: PLC0415 — same import purity as _serve()

    pid = _answered_pid(daemon_contract) or _listening_pid(daemon_contract.PORT_NUMBER)
    return pid if pid and process_is_alive(pid) else 0


def _answered_pid(daemon_contract: ModuleType) -> int:
    """The pid the daemon reports for itself, or 0 if it does not report one."""
    connection = http.client.HTTPConnection(
        daemon_contract.HOST_ADDRESS, daemon_contract.PORT_NUMBER,
        timeout=HEALTH_TIMEOUT_SECONDS,
    )
    try:
        connection.request("GET", HEALTH_PATH)
        response = connection.getresponse()
        if response.status != 200:
            return 0
        return HealthProcess.model_validate_json(response.read()).process_id
    except (OSError, ValueError, KeyError, TypeError):
        return 0
    finally:
        connection.close()


def _listening_pid(port: int) -> int:
    """Whoever holds the port, when it no longer answers. Best effort: if lsof
    is not installed there is nothing to signal and nothing to report."""
    try:
        found = subprocess.run(
            ["lsof", "-nP", "-iTCP:%d" % port, "-sTCP:LISTEN", "-t"],
            capture_output=True, text=True, check=False, timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    first = found.stdout.split()
    return int(first[0]) if first and first[0].isdigit() else 0


def url() -> str:
    # the daemon contract, not the server facade: the bind address is
    # core/daemon/contract.py's to own, and a lazy import keeps this module import-pure
    # like _serve() does (the application runtime owns the server import).
    from core.daemon import contract as daemon_contract  # noqa: PLC0415 — same import purity as _serve()
    return "http://%s:%d" % (daemon_contract.HOST_ADDRESS, daemon_contract.PORT_NUMBER)


def start(flags: list[str] | None = None) -> int:
    if holder():
        print("dashboard already running · %s" % url())
        return 0
    from dashboard import paths  # noqa: PLC0415 — import purity, and more: this
    # module resolves the DATA DIRECTORY at import, which pulls the port contract
    # with it. Imported here rather than at the top so `main` has already applied
    # `--port` and `--data-dir` to the environment those two read — a constant
    # frozen before the flags were parsed is a flag that silently does nothing,
    # measured: `serve --port 8794` bound 8377 and died on the busy port.

    entry = os.path.join(paths.BIN_DIRECTORY, "baqylau-dashboard.py")
    command = [sys.executable, entry, "serve", *(flags or [])]
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        record.error("", "spawn web dashboard", PathAudit(path=entry))
        print("dashboard failed to spawn (see audit errors)", file=sys.stderr)
        return 1
    record.spawn("", process.pid, command[1:], purpose="web dashboard")
    for _ in range(40):                     # ~2s for the port to answer
        if holder():
            break
        time.sleep(0.05)
    print("dashboard started · %s" % url())
    return 0


def stop() -> int:
    pid = holder()
    if not pid:
        print("dashboard not running")
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
        print("dashboard stopped (pid %d)" % pid)
        return 0
    except OSError as e:
        print("stop failed: %s" % e, file=sys.stderr)
        return 1


def status() -> int:
    pid = holder()
    if pid:
        print("running · pid %d · %s" % (pid, url()))
    else:
        print("not running")
    return 0


def open_browser() -> int:
    rc = start()
    if rc:
        return rc
    try:
        subprocess.run(["open", url()], check=False)   # macOS; harmless no-op elsewhere
    except OSError:
        pass
    return 0


def rebuild() -> int:
    """Re-derive `session_data`, `session_data_actors` and `session_entries`.

    The insurance the push-based read model needs: if a writer was wrong, or
    crashed halfway, the facts are all still in `canonical_events` and the whole
    read model can be folded again from them. Runs the WRITERS only — a replay
    that also ran the side-effect reactions would reopen the panes of every
    session that ever finished.

    Not a bin script of its own: `tests/test_canonical_architecture.py` keeps the
    entry points to the two the clients need, and this is an operator's command
    on the daemon they already have.
    """
    from app import providers  # noqa: PLC0415 — import purity: the graph is the daemon's
    from app.injection import registry, resolve  # noqa: PLC0415

    pid = holder()
    if pid:
        print("stop the dashboard first: one writer, or the rebuild races it", file=sys.stderr)
        return 1
    loop = resolve(registry(), providers.reaction_loop)
    print("rebuilding the read model…")
    total = loop.rebuild()
    print("rebuilt %d facts" % total)
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "open"
    try:
        options = _options(argv[2:])
    except UsageError as error:
        print("%s\n%s" % (error, __doc__ or ""), file=sys.stderr)
        return 2
    # Applied BEFORE anything that reads them is imported: this module's imports
    # of the port contract and the server are lazy for exactly this reason, so
    # every module below resolves against the environment this call just decided.
    os.environ.update(options.variables)
    if cmd == "serve":
        if options.log_path is not None:
            _redirect(options.log_path)
        return _serve(options.harness_runtime_configs)
    if cmd == "start":
        return start(_forwarded(argv[2:]))
    if cmd == "stop":
        return stop()
    if cmd == "status":
        return status()
    if cmd == "open":
        return open_browser()
    if cmd == "rebuild":
        return rebuild()
    print(__doc__ or "usage: baqylau-dashboard.py [serve|start|stop|status|open|rebuild]",
          file=sys.stderr)
    return 2

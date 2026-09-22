# Copyright (c) 2026 Zhambyl Yermagambet
"""baqylau-dashboard [serve|start|stop|status|open] [options].

The web dashboard's CLI lifecycle. The implementation behind the thin
bin/baqylau-dashboard entry (that filename is operational audit
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
  --extension-root DIR  a package root; repeat for more roots
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

import os

from dashboard.cli_forwarding import forwarded_flags
from dashboard.cli_lifecycle import open_browser, start, status, stop
from dashboard.cli_models import _DashboardOptions
from dashboard.cli_options import launch_options
from dashboard.cli_output import UsageError, _error
from dashboard.cli_rebuild import rebuild
from dashboard.cli_server import _redirect, _serve


def main(argv: list[str]) -> int:
    """Run the command.

    Returns:
        Integer result.

    """
    cmd = argv[1] if len(argv) > 1 else "open"
    try:
        options = launch_options(argv[2:])
    except UsageError as error:
        module_help = __doc__ or ""
        _error(f"{error}\n{module_help}")
        return 2
    # Applied BEFORE anything that reads them is imported: this module's imports
    # of the port contract and the server are lazy for exactly this reason, so
    # every module below resolves against the environment this call just decided.
    os.environ.update(options.variables)
    if cmd == "serve":
        return _serve_command(options)
    if cmd == "start":
        return start(forwarded_flags(argv[2:]))
    return _run_standard_command(cmd)


def _serve_command(dashboard_options: _DashboardOptions) -> int:
    if dashboard_options.log_path is not None:
        _redirect(dashboard_options.log_path)
    return _serve(dashboard_options.harness_runtime_configs)


def _run_standard_command(command_name: str) -> int:
    command = {
        "stop": stop,
        "status": status,
        "open": open_browser,
        "rebuild": rebuild,
    }.get(command_name)
    if command is not None:
        return command()
    _error(__doc__ or "usage: baqylau-dashboard [serve|start|stop|status|open|rebuild]")
    return 2

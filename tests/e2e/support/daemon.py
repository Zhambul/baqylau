"""The daemon under test: the real `serve` process, on its own port and databases.

Deliberately NOT an in-process application, and deliberately not a private way
of starting one. The command below is the command a person types —

    .venv/bin/python bin/baqylau-dashboard.py serve --port N --data-dir DIR --log FILE

— so what this suite exercises is the launch path the machine uses, and a rig
that composed the routes itself, or reached for environment variables the CLI
does not document, would be testing a different program than the one that breaks.
The port bind IS the daemon's singleton guard, so a free port is claimed per run
and the developer's own daemon on 8377 keeps running untouched beside it.

Three things still ride the ENVIRONMENT rather than the command line, because
they describe the world the daemon finds itself in rather than how this launch is
parameterised: which terminal exists (`BAQYLAU_TERMINAL`), and whether the two
notification channels are configured. A person does not type those either — they
live in a shell profile or a service unit — so passing them here is the same
thing a person's environment does, not a private channel.
"""

from __future__ import annotations

import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import TypeVar

from pydantic import TypeAdapter, ValidationError

from api.common.models.replies.health_response import HealthResponse
from support.environment import child_environment

Decoded = TypeVar("Decoded")

HEALTH_PATH = "/api/health"
# The daemon is up when its health route answers ITS model — not merely when
# something parses as JSON.
HEALTH = TypeAdapter(HealthResponse)
START_TIMEOUT_SECONDS = 30.0
STOP_TIMEOUT_SECONDS = 10.0
READ_TIMEOUT_SECONDS = 10.0
POLL_SECONDS = 0.1


def free_port() -> int:
    """A port nobody holds right now. Raced in principle, never in practice —
    and a collision fails loudly at startup rather than corrupting a run."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@dataclass
class Daemon:
    """A running daemon and the two ways this suite talks to it: HTTP, and the
    databases it writes (read directly, for the interpretation verdicts that
    have no route of their own)."""

    port: int
    data_directory: str
    log_path: str
    process: subprocess.Popen[bytes]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def main_database_path(self) -> str:
        return os.path.join(self.data_directory, "main.db")

    @property
    def audit_database_path(self) -> str:
        return os.path.join(self.data_directory, "audit.db")

    def read(self, path: str, shape: TypeAdapter[Decoded]) -> Decoded:
        """One GET, decoded into the model the ROUTE declares it answers with.

        `shape` is the daemon's own published response model (support/observe.py
        holds one adapter per resource), so a reply that no longer matches the
        contract fails HERE, naming the field — rather than surfacing four
        frames later as a KeyError on a dict, or, worse, as `.get("state")`
        quietly returning None and a scenario waiting out its timeout for a
        state that was never going to be spelled that way again.

        Raises on anything but a 200 — a rig that swallowed a 500 would report
        the absence of an item as a product bug.
        """
        body = self.get_text(path)
        try:
            return shape.validate_json(body)
        except ValidationError as error:
            raise AssertionError(f"GET {path} did not answer its own contract: {error}") from error

    def post(self, path: str, document: dict[str, object]) -> tuple[int, str]:
        """One control-plane POST, with the status: a REFUSAL is a verdict here,
        not an error, so the caller decides what a 409 means rather than being
        handed an exception.

        Carries the same caller-proof header a client does (`client/_http.py`),
        because `api/guard.py` rejects a mutating request without it — this suite
        talks to the control plane exactly the way the browser and the hooks do.
        """
        body = json.dumps(document).encode("utf-8")
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.port, timeout=READ_TIMEOUT_SECONDS
        )
        try:
            connection.request("POST", path, body, {
                "Content-Type": "application/json",
            })
            response = connection.getresponse()
            return response.status, response.read().decode("utf-8", "replace")
        finally:
            connection.close()

    def get_text(self, path: str) -> str:
        """One GET, undecoded — for the routes that answer something other than
        one of the models `read` validates against."""
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=READ_TIMEOUT_SECONDS)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            body = response.read()
            if response.status != 200:
                raise AssertionError(f"GET {path} -> {response.status}: {body[:400]!r}")
            return body.decode("utf-8", "replace")
        finally:
            connection.close()

    def log(self) -> str:
        try:
            with open(self.log_path, encoding="utf-8", errors="replace") as source:
                return source.read()
        except OSError:
            return ""

    def stop(self) -> None:
        if self.process.poll() is not None:
            return
        self.process.send_signal(signal.SIGTERM)
        try:
            self.process.wait(timeout=STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=STOP_TIMEOUT_SECONDS)


def start(repository_root: str, data_directory: str, port: int) -> Daemon:
    """Spawn the daemon and return once its health route answers.

    Every per-run difference is a FLAG, so this list is the command line and
    nothing is hidden in the environment that the CLI would rather have been
    told: the port it binds, the directory both databases live in, and the file
    its own output goes to.
    """
    os.makedirs(data_directory, exist_ok=True)
    log_path = os.path.join(data_directory, "daemon.log")
    entry = os.path.join(repository_root, "bin", "baqylau-dashboard.py")
    process = subprocess.Popen(
        [
            sys.executable, entry, "serve",
            "--port", str(port),
            "--data-dir", data_directory,
            "--log", log_path,
        ],
        cwd=repository_root,
        env=child_environment(
            # The daemon owns the harness's terminal, and it is a REAL one:
            # pseudo-terminals rather than kitty tabs, which is what lets this
            # run headless. Every launch and every keystroke in this suite
            # therefore goes through the product's own launch route and terminal
            # adapter — the path a person's launch takes — instead of a private
            # handle the test opened for itself.
            BAQYLAU_TERMINAL="pty",
            # A test run must not reach the developer's phone.
            BAQYLAU_DASHBOARD_NOTIFY_TELEGRAM="0",
            BAQYLAU_DASHBOARD_NOTIFY_WEBPUSH="0",
        ),
        stdin=subprocess.DEVNULL,
        # No redirect of our own: `--log` is the daemon's answer to where its
        # output goes, and two mechanisms for one question is one too many.
        start_new_session=True,
    )
    daemon = Daemon(port=port, data_directory=data_directory, log_path=log_path, process=process)
    deadline = time.monotonic() + START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"daemon exited with {process.returncode}\n{daemon.log()}")
        try:
            daemon.read(HEALTH_PATH, HEALTH)
            return daemon
        except (OSError, AssertionError):
            time.sleep(POLL_SECONDS)
    daemon.stop()
    raise AssertionError(f"daemon did not answer {HEALTH_PATH} in {START_TIMEOUT_SECONDS}s\n{daemon.log()}")

# plugins/otel/ — the OpenTelemetry cost/token pipeline.
#
# Unlike claude_code (a host) or codex (a per-session secondary source), this
# plugin owns ONE per-machine background process: a local OTLP metrics receiver
# that ingests Claude Code's claude_code.token.usage / cost.usage exports and
# writes the per-session scoreboard counters (tk_*/cost/tokens) the transcript
# fold used to. It is the AUTHORITATIVE cost source; the transcript fold survives
# only as a SessionEnd fallback (plugins/claude_code/stop_fmt.py) for sessions that
# ran without telemetry. See plugins/otel/receiver.py for the full rationale, and
# README § Scoreboard.
#
# Requires the telemetry env in ~/.claude/settings.json (README § Wiring):
#   CLAUDE_CODE_ENABLE_TELEMETRY=1, OTEL_METRICS_EXPORTER=otlp,
#   OTEL_EXPORTER_OTLP_PROTOCOL=http/json,
#   OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:<CLAUDE_OTEL_PORT>,
#   OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta.
import os
import subprocess
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def on_session_start(log, cwd, sid):
    """Spawn-if-not-running the global OTLP receiver on every host SessionStart.
    Runs the detach-fast launcher (which Popens the receiver DETACHED and returns
    in a few ms — SessionStart can never hang on it, the codex lesson). The
    receiver self-arbitrates the per-machine singleton, so calling this from every
    session is safe."""
    # Only run a receiver if this session actually exports telemetry — otherwise
    # there is nothing to receive (and hermetic tests, which don't set this, stay
    # inert). The env is set in ~/.claude/settings.json (README § Wiring).
    if os.environ.get("CLAUDE_CODE_ENABLE_TELEMETRY") != "1":
        return
    launcher = os.path.join(_REPO, "claude-otlp-launch.py")
    if not os.path.isfile(launcher):
        return
    try:
        subprocess.run([sys.executable or "python3", launcher],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

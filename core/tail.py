# core/tail.py — the shared skeleton of every detached tailer
# (Importable as `claude_tail` via the top-level compat shim.)
# (claude-stream.py, claude-substream.py, claude-codex-stream.py).
#
# Each tailer is the same machine: follow a growing file by byte position,
# surface complete lines (holding a trailing partial line until its newline
# arrives), poll until a kind-specific completion signal, and wrap the whole run
# in an audited stream lifecycle (streams row + crash-audit-then-swallow).
# Before this module all of that was copy-pasted three times — including the
# read-exactly-size-minus-pos subtlety, comment and all.
import os
import time

try:
    from core import audit as A          # always-on audit trail (CLAUDE_AUDIT=0 disables)
except Exception:                       # audit must never break a tailer
    class _NoAudit:
        def __getattr__(self, _):
            return lambda *a, **k: None
    A = _NoAudit()

# Env overrides exist solely for the test suite (README § Testing) — real
# sessions never set them, so the shipped cadence stays the literal defaults.
POLL_S = float(os.environ.get("CLAUDE_TAIL_POLL_S") or 0.4)
                            # main poll cadence of every tailer loop
BACKSTOP_S = float(os.environ.get("CLAUDE_TAIL_BACKSTOP_S") or 6 * 3600)
                            # absolute cap so a stuck/lost tailer can't run forever


class FileTailer:
    """Byte-position tailer over one growing file.

    pump() returns the newly completed lines (bytes, newline-stripped) since the
    last call — or None when the file has VANISHED (callers treat that as their
    own end signal). A trailing partial line is held in `pending` until its
    newline arrives, so lines are never split. `consumed` is the byte offset of
    everything actually surfaced (pos minus the held partial) — what a resume
    checkpoint must record. `idle_for(now)` is how long the file size has been
    unchanged (growth without a newline still counts as activity)."""

    def __init__(self, path, pos=0):
        self.path = path
        self.pos = pos
        self.pending = b""
        self.size = -1              # last observed size (-1: never seen)
        self.changed_at = time.time()

    def pump(self):
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return None
        if size < self.pos:
            # The file SHRANK (truncated / rewritten in place, e.g. the command
            # ran `> file` again): our offset points past EOF, so nothing would
            # ever be emitted again — and a regrow past the old offset would
            # resume mid-content from a stale position. A truncating writer
            # means the content is fresh: start over from 0.
            self.pos = 0
            self.pending = b""
            self.changed_at = time.time()
        if size > self.size:
            self.changed_at = time.time()
        self.size = size
        lines = []
        if size > self.pos:
            try:
                # Read exactly size-pos bytes: an unbounded read() can grab bytes
                # appended DURING the read, which `pos = size` would then not
                # account for — the next pump would re-read and duplicate them.
                with open(self.path, "rb") as fh:
                    fh.seek(self.pos)
                    chunk = fh.read(size - self.pos)
                    self.pending += chunk
                    self.pos += len(chunk)
            except OSError:
                return lines
            *lines, self.pending = self.pending.split(b"\n")
        return lines

    @property
    def consumed(self):
        return self.pos - len(self.pending)

    def idle_for(self, now=None):
        return (now or time.time()) - self.changed_at


def wait_for(path, deadline, alive=None):
    """Poll until `path` exists (True) or the deadline passes / `alive()` turns
    false (False). Every tailer starts with this: its source file can land a
    beat after the spawning hook fires."""
    while time.time() < deadline:
        if os.path.exists(path):
            return True
        if alive is not None and not alive():
            return False
        time.sleep(0.2)
    return os.path.exists(path)


class stream_lifecycle:
    """The audited shell every detached tailer's __main__ repeats: register the
    audit streams row on entry; on exit run the (always-run) cleanup, close the
    row with the end reason, and SWALLOW any crash — auditing it first, per the
    CLAUDE.md invariant. Usage:

        with stream_lifecycle(LOG, "bg", task_id=..., src_path=...,
                              ctx={...}, on_exit=release_slot) as run:
            ...
            run.end("writer-gone")
            run.lines = n_emitted   # optional; None (never set) stays NULL

    A run that never calls end() closes as "?" (same as before), and a crash
    closes as "crash". `ctx` lands in the error row's context column."""

    def __init__(self, log, kind, agent_id="", task_id="", src_path="",
                 ctx=None, on_exit=None):
        self.log, self.kind = log, kind
        self.agent_id, self.task_id, self.src_path = agent_id, task_id, src_path
        self.ctx, self.on_exit = ctx, on_exit
        self.stream_id, self.end_reason, self.lines = None, "?", None

    def end(self, reason):
        self.end_reason = reason

    def __enter__(self):
        self.stream_id = A.stream_start(self.log, self.kind,
                                        agent_id=self.agent_id,
                                        task_id=self.task_id,
                                        src_path=self.src_path)
        return self

    def __exit__(self, et, ev, tb):
        if et is not None:
            self.end_reason = "crash"
            A.error(self.log, "main", self.ctx)
        if self.on_exit is not None:
            try:
                self.on_exit()
            except Exception:
                A.error(self.log, "stream on_exit", self.ctx)
        A.stream_end(self.stream_id, self.end_reason, self.lines)
        return True                 # tailers must never leak an exception

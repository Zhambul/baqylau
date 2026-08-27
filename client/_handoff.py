# client/_handoff.py — the local channel between a pane and its click handlers.
#
# A click on a link in the mirror does not reach the pane process: the terminal
# launches a NEW program with the URI (kitty's open-actions), and that program
# has no model, no stream and — by decision — no daemon to ask. Content is
# embedded in the entries the pane already holds, so both gestures are the
# frontend's own and the daemon is not involved in either.
#
# So the two processes meet on disk, and the shape is chosen to make races
# impossible rather than to handle them: TWO files, each with exactly ONE writer.
#
#   <dir>/baqylau-pane-<uid>-<session>-<kind>.json    written by the PANE only
#       {"pid": 4321, "targets": {"<id>": "<text>", …}}
#   <dir>/baqylau-view-<uid>-<session>-<kind>.json    written by the HANDLER only
#       {"opened": ["<entry-id>", …]}
#   <dir>/baqylau-pane-<uid>-<session>-<kind>.lock    HELD by the pane, flock'd
#
# The pane publishes what is on screen and its own pid; the handler publishes
# what the reader has expanded and then signals that pid. Neither ever writes the
# other's file, so a half-written file is only ever a file the other side
# re-reads next tick.
#
# The LOCK is what makes the signal safe, and it is not optional. A pid in a file
# is a pid that was true once: a pane that died leaves its file behind, the
# system recycles the number, and a handler that trusted it would fire SIGUSR1 at
# whatever now holds it — someone else's process, for whom that signal means
# something else entirely, or nothing and therefore death. So the pane holds an
# exclusive flock for its whole life, and the handler signals only when it CANNOT
# take that lock. A lock nobody holds is a pane that is gone.
#
# Import-pure. The pane process uses Pydantic for this file boundary.
from __future__ import annotations

import fcntl
import os
import signal
import tempfile
from collections.abc import Mapping
from typing import IO, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

# The uid is in the NAME, not in a directory mode: on a shared /tmp two people
# running this must not collide, and a name is checked by the filesystem for
# free. (macOS already gives each user its own TMPDIR; Linux does not.)
PANE_FILE = "baqylau-pane-%d-%s-%s.json"
VIEW_FILE = "baqylau-view-%d-%s-%s.json"
LOCK_FILE = "baqylau-pane-%d-%s-%s.lock"
# What a handler sends to make the pane re-read the view file and repaint. SIGUSR1
# because the pane already lives on signals — a resize and the clock are the same
# mechanism — and because it is the one channel that reaches a process blocked in
# a socket read without a second thread.
REPAINT_SIGNAL = signal.SIGUSR1
# One copy target is a command's output. Capped so that a runaway build log
# cannot turn every repaint into a multi-megabyte write; a clipboard nobody can
# read past the first screenful loses nothing real.
TARGET_LIMIT = 256 * 1024

T = TypeVar("T", bound=BaseModel)


class PaneDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pid: int
    targets: Mapping[str, str]


class ViewDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    opened: tuple[str, ...]


def _path(template: str, session_id: str, kind: str) -> str:
    return os.path.join(
        tempfile.gettempdir(),
        template % (os.getuid(), _safe(session_id), _safe(kind)),
    )


def _safe(value: str) -> str:
    """A path component from something a harness chose.

    Session ids are uuid-shaped in practice, but they are the harness's to pick,
    and one with a slash in it would otherwise write outside the directory.
    """
    return "".join(character if character.isalnum() or character in "-_" else "_"
                   for character in value)[:120] or "unnamed"


def pane_path(session_id: str, kind: str) -> str:
    return _path(PANE_FILE, session_id, kind)


def view_path(session_id: str, kind: str) -> str:
    return _path(VIEW_FILE, session_id, kind)


def lock_path(session_id: str, kind: str) -> str:
    return _path(LOCK_FILE, session_id, kind)


# The pane's own lock, kept at module scope for the reason locks always are: it
# lives exactly as long as the process, and a file object that went out of scope
# would be closed by the collector and release it.
_held: IO[str] | None = None


def hold(session_id: str, kind: str) -> bool:
    """Claim this session-and-kind as the live pane. False if another one has it.

    False is not an error and not a reason to exit: two panes on one session are
    allowed and both paint correctly. It only decides which of them a CLICK wakes,
    and the first one there wins — the same rule the terminal itself applies when
    it decides which window a link was clicked in.
    """
    global _held
    try:
        holder = open(lock_path(session_id, kind), "w", encoding="utf-8")
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    _held = holder
    return True


def _read(path: str, model: type[T]) -> T | None:
    """Whatever is there, or nothing. A missing file means the pane is not
    running; a malformed one means it was mid-write. Both are "nothing yet",
    and neither is worth failing a click over."""
    try:
        with open(path, "rb") as source:
            return model.model_validate_json(source.read())
    except (OSError, ValidationError):
        return None


def _write(path: str, document: BaseModel) -> None:
    """Write, then rename. The rename is atomic, so a reader either sees the
    previous whole file or the next one — never half of either."""
    temporary = "%s.%d.tmp" % (path, os.getpid())
    try:
        with open(temporary, "w", encoding="utf-8") as sink:
            sink.write(document.model_dump_json())
        os.replace(temporary, path)
    except OSError:
        # The handoff is a convenience; a pane that cannot write it still paints.
        try:
            os.unlink(temporary)
        except OSError:
            pass


def publish(session_id: str, kind: str, targets: Mapping[str, str]) -> None:
    """The pane says what is on screen and where to find it."""
    os.umask(0o077)
    published_targets = {
        name: text[:TARGET_LIMIT] for name, text in targets.items()
    }
    _write(
        pane_path(session_id, kind),
        PaneDocument(pid=os.getpid(), targets=published_targets),
    )


def target(session_id: str, kind: str, name: str) -> str | None:
    """The text behind one copy link, as the pane last published it."""
    found = _read(pane_path(session_id, kind), PaneDocument)
    if found is None:
        return None
    return found.targets.get(name)


def opened(session_id: str, kind: str) -> frozenset[str]:
    """Which entries the reader has expanded."""
    found = _read(view_path(session_id, kind), ViewDocument)
    return frozenset() if found is None else frozenset(found.opened)


def toggle(session_id: str, kind: str, entry_id: str) -> bool:
    """Flip one entry's expanded state and say what it became."""
    current = set(opened(session_id, kind))
    became = entry_id not in current
    if became:
        current.add(entry_id)
    else:
        current.discard(entry_id)
    os.umask(0o077)
    _write(view_path(session_id, kind), ViewDocument(opened=tuple(sorted(current))))
    return became


def wake(session_id: str, kind: str) -> bool:
    """Ask the pane to re-read and repaint. False when there is no pane to ask.

    The lock is checked BEFORE the pid is used, and that order is the whole
    safety of this function: a pid nobody vouches for is a pid that may belong to
    a stranger.
    """
    if not _pane_is_running(session_id, kind):
        return False
    found = _read(pane_path(session_id, kind), PaneDocument)
    if found is None:
        return False
    try:
        os.kill(found.pid, REPAINT_SIGNAL)
    except OSError:
        return False              # it exited between the two checks
    return True


def _pane_is_running(session_id: str, kind: str) -> bool:
    """Whether a live pane holds this session-and-kind.

    Asked by TRYING to take the lock: if it can be taken, nobody holds it, and
    the pane whose pid is in the file beside it is gone.
    """
    try:
        probe = open(lock_path(session_id, kind), "a", encoding="utf-8")
    except OSError:
        return False
    try:
        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True                       # somebody holds it: a pane is alive
    finally:
        probe.close()                     # closing releases whatever we took
    return False

# terminal/impl/kitty/remote.py — the transport to a running kitty.
#
# Talking to kitty happens over the socket in $KITTY_LISTEN_ON, either through
# the `kitten` client or, on the hot paths, as a raw write of the same bytes
# the client sends. Never the TTY: hooks run with no controlling terminal.
#
# Everything here is best-effort and silent — a failed call returns rc 1 / [] /
# None and never raises, because every caller above is a hook or a render loop
# that must not fail on a terminal that went away.
import dataclasses
import glob
import json
import os
import stat
import subprocess
import shutil
import time

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from terminal.models.values import WindowId

# kitty's own JSON, read back — GENUINELY open (`extra="ignore"`): an `ls`
# tree and an `@kitty-cmd` reply both carry many fields (title, pid, cwd,
# foreground_processes, ...) this module never reads one of. Declared as far
# as reality allows: the fields `windows()`/`app_focused()`/`raw()`'s callers
# actually read.
FOREIGN = ConfigDict(extra="ignore", frozen=True)


class KittyProcess(BaseModel):
    model_config = FOREIGN
    pid: int | None = None
    cmdline: list[str] | None = None


class KittyWindowInfo(BaseModel):
    model_config = FOREIGN
    id: int | str | None = None
    columns: int | None = None
    lines: int | None = None
    user_vars: dict[str, str] | None = None
    foreground_processes: list[KittyProcess] | None = None
    is_active: bool | None = None


class KittyTab(BaseModel):
    model_config = FOREIGN
    id: int | str | None = None
    is_active: bool | None = None
    is_focused: bool | None = None
    windows: list[KittyWindowInfo] | None = None


class KittyOSWindow(BaseModel):
    model_config = FOREIGN
    is_focused: bool | None = None
    tabs: list[KittyTab] | None = None


class KittyRcResponse(BaseModel):
    """A `@kitty-cmd` reply — `ok` and, for `get-text` only, `data`; every
    other command answers with `ok` alone."""

    model_config = FOREIGN
    ok: bool = False
    data: str | None = None


@dataclasses.dataclass(frozen=True)
class SetTabColorRcPayload:
    match: str
    colors: dict[str, int | None]


@dataclasses.dataclass(frozen=True)
class GetTextRcPayload:
    match: str
    extent: str
    ansi: bool = False


@dataclasses.dataclass(frozen=True)
class LsRcPayload:
    """The kitty `ls` command has no required payload fields."""


KittyRcPayload = SetTabColorRcPayload | GetTextRcPayload | LsRcPayload

# The variable kitty exports into every process it starts in a window. Named
# rather than inlined because a stdlib-only client observes its own window from
# INSIDE it and cannot import a plugin to ask: its copy of this name
# (`client/_http.py`) is pinned to this one by the suite.
WINDOW_ID_VARIABLE = "KITTY_WINDOW_ID"

# Timeout for mutating `kitten @` calls (run): kitten has its own client-side
# response timeout, but a hang on socket CONNECT is unbounded, and every split
# op (and every tab paint whose raw-socket attempt missed) runs through here
# from hook processes — which must never block.
KITTEN_TIMEOUT_SECONDS = 10
# Tighter timeout for read-only queries (get-text / ls): they run on hot paths
# (renderer reflow, geometry probes) where a stale answer is useless anyway.
KITTEN_QUERY_TIMEOUT_SECONDS = 5
# Timeout for a raw unix-socket remote-control exchange (raw): the whole point
# of the raw path is sub-millisecond latency, so give up fast and let the
# caller fall back to the kitten subprocess.
REMOTE_CONTROL_SOCKET_TIMEOUT_SECONDS = 0.5
# Gap between a text write and its Enter (CR) write. Delivered in the SAME
# write, a harness TUI's chunk-based paste detection sometimes read text+CR as
# one pasted chunk, turning the CR into a draft newline instead of a submit
# (timing-dependent → intermittent). The gap makes the CR arrive as its own
# stdin read = an unambiguous Enter keypress.
SEND_ENTER_DELAY_SECONDS = 0.15
# The remote-control protocol version stamped into every @kitty-cmd command
# (what a current kitten client sends; kitty accepts any version <= its own).
KITTY_RC_VERSION = [0, 26, 0]
# The @kitty-cmd socket framing: ESC P (DCS) + key + {json} + ESC \ (ST). The
# reply, when requested, is framed the same way — locate its payload by the
# key, not the DCS introducer (the reply may arrive mid-buffer).
RC_CMD_KEY = b"@kitty-cmd"
RC_CMD_DCS = b"\x1bP" + RC_CMD_KEY
RC_ST = b"\x1b\\"
# The set-tab-color sentinel that clears a colour back to the theme default —
# clearing paints all four channels with it.
TAB_COLOR_NONE = "NONE"


def find_kitten() -> str | None:
    """Locate the kitten binary: $KITTY_KITTEN_BIN override, PATH, then the macOS
    app bundle. None when kitty isn't installed."""
    k = os.environ.get("KITTY_KITTEN_BIN")
    if k:
        return k
    k = shutil.which("kitten")
    if k:
        return k
    bundle = "/Applications/kitty.app/Contents/MacOS/kitten"
    return bundle if os.access(bundle, os.X_OK) else None


def _is_socket(p: str) -> bool:
    try:
        return stat.S_ISSOCK(os.stat(p).st_mode)
    except OSError:
        return False


def resolve_listen_on() -> str:
    """The controlling kitty instance's socket when $KITTY_LISTEN_ON is absent
    (a keymap-driven `launch --type=background` child does NOT inherit it):
    listen_on `unix:/tmp/kitty` yields `/tmp/kitty-<kitty-pid>`, and that kitty
    pid is an ancestor of this process. Uses the lone socket when exactly one
    kitty instance exists."""
    if os.environ.get("KITTY_LISTEN_ON"):
        return os.environ["KITTY_LISTEN_ON"]
    pid = os.getppid()
    while pid and pid > 1:
        if _is_socket(f"/tmp/kitty-{pid}"):
            return f"unix:/tmp/kitty-{pid}"
        try:
            out = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
            pid = int(out)
        except (ValueError, OSError):
            break
    socks = [s for s in glob.glob("/tmp/kitty-*") if _is_socket(s)]
    if len(socks) == 1:
        return "unix:" + socks[0]
    return ""


def current_window_id() -> str:
    """The kitty window this process runs in, or "".

    The one place $KITTY_WINDOW_ID is read inside the application; every other
    component receives the answer as data."""
    return os.environ.get(WINDOW_ID_VARIABLE, "")


class KittyRemote:
    """One kitty control channel.

    `listen` is resolved PER CALL, not cached: the daemon outlives kitty
    instances, and the socket path carries kitty's pid — a channel pinned at
    bootstrap would go permanently dead the first time kitty restarted.
    """

    def __init__(self, listen: str | None = None, kitten: str | None = None) -> None:
        self._pinned_listen = listen
        self.kitten: str | None = kitten if kitten is not None else find_kitten()

    @property
    def listen(self) -> str:
        if self._pinned_listen is not None:
            return self._pinned_listen
        return resolve_listen_on()

    # --- the kitten client ---------------------------------------------------
    def run(self, *args: str) -> int:
        """A silenced `kitten @ …` call; the exit code (1 on any failure)."""
        if self.kitten is None:
            return 1
        try:
            return subprocess.run(
                [self.kitten, "@", "--to", self.listen, *args],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=KITTEN_TIMEOUT_SECONDS,
            ).returncode
        except Exception:
            return 1

    def capture(self, *args: str, timeout: float = KITTEN_TIMEOUT_SECONDS) -> str | None:
        """A `kitten @ …` call whose stdout is the answer, or None on failure."""
        if self.kitten is None:
            return None
        try:
            r = subprocess.run([self.kitten, "@", "--to", self.listen, *args], capture_output=True, timeout=timeout)
        except Exception:
            return None
        if r.returncode != 0:
            return None
        return r.stdout.decode("utf-8", "replace")

    def ls(self) -> list[KittyOSWindow] | None:
        """Parsed `kitten @ ls` (the OS-window/tab/window tree), or `None` when
        the query itself failed (a timeout, a dropped socket, bad output —
        including output that no longer matches the shape declared above).

        `None` is not `[]`: a caller who needs to tell "kitty reports zero
        windows" apart from "kitty could not be asked right now" can. Getting
        this distinction wrong is what makes a five-second query hiccup read
        as EVERY window having just closed — safe for a caller that only
        paints the screen, wrong for one that decides whether to push an
        alert about a session that is still there."""
        response = self.raw("ls", LsRcPayload(), want_response=True)
        out = response.data if isinstance(response, KittyRcResponse) and response.ok else None
        if out is None:
            out = self.capture("ls", timeout=KITTEN_QUERY_TIMEOUT_SECONDS)
        if out is None:
            return None
        try:
            return TypeAdapter(list[KittyOSWindow]).validate_json(out)
        except ValidationError:
            return None

    def app_focused(
        self,
        tree: list[KittyOSWindow] | None = None,
    ) -> bool:
        """True when ANY kitty OS window is focused — i.e. kitty is the frontmost
        app on this desktop right now. The gate for a pane launch's
        --keep-focus: kitty's keep-focus "restore the previous window" path
        calls focus_os_window(raise=True) whenever no kitty OS window is
        focused, which on macOS ACTIVATES the kitty app over whatever the user
        is in (the dashboard web-launch steal — the panes opened at
        SessionStart were the thieves). `tree` reuses an ls() the caller
        already paid for. False on an ls failure (degrade toward not
        stealing) — unlike `windows()`, a focus probe has no earlier answer
        worth repeating, so its failure default stays "not focused"."""
        try:
            return any(osw.is_focused for osw in ((self.ls() if tree is None else tree) or []))
        except Exception:
            return False

    def send_text(self, win: str, text: str, bracketed: bool = False) -> bool:
        """`kitten @ send-text --stdin` to window `win`: the text goes over STDIN
        precisely so it is never a shell argument NOR a kitten escape vector —
        `--stdin` sends the bytes verbatim, no `\\n`/`\\x1b` interpretation. The
        Enter (CR) is a SEPARATE second call after SEND_ENTER_DELAY_SECONDS (see
        its comment: one write let paste detection swallow the CR into the
        draft). True only when both writes rc 0.

        `bracketed=True` wraps the text in bracketed-paste escapes so the TUI
        reads it as ONE atomic paste — needed for the cancel-edit resend, where
        a raw send into an input whose state just changed drops the leading
        bytes (measured). The CR stays OUTSIDE the paste, so it still submits."""
        if self.kitten is None:
            return False
        try:
            argv = [self.kitten, "@", "--to", self.listen, "send-text", "--match", f"id:{win}", "--stdin"]
            text_argv = argv[:-1] + ["--bracketed-paste=enable", "--stdin"] if bracketed else argv
            r = subprocess.run(
                text_argv,
                input=text.encode("utf-8"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=KITTEN_TIMEOUT_SECONDS,
            )
            if r.returncode != 0:
                return False
            time.sleep(SEND_ENTER_DELAY_SECONDS)
            r = subprocess.run(
                argv, input=b"\r", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=KITTEN_TIMEOUT_SECONDS
            )
            return r.returncode == 0
        except Exception:
            return False

    def get_text(self, win_id: WindowId, extent: str = "screen", ansi: bool = False) -> str | None:
        """`kitten @ get-text` for a window, or None on failure. extent="screen"
        is the VISIBLE viewport — verified live: a window scrolled up returns the
        scrolled-to rows, not the live screen's bottom — which is what lets the
        mirror renderer restore the exact scroll position across a reflow."""
        argv = ["get-text", "--match", f"id:{win_id}", "--extent", extent]
        if ansi:
            argv.append("--ansi")
        return self.capture(*argv, timeout=KITTEN_QUERY_TIMEOUT_SECONDS)

    # --- the raw socket ------------------------------------------------------
    def raw(
        self,
        cmd: str,
        payload: KittyRcPayload,
        want_response: bool = False,
        timeout: float = REMOTE_CONTROL_SOCKET_TIMEOUT_SECONDS,
    ) -> KittyRcResponse | bool | None:
        """A remote-control command over a RAW unix-socket write of the
        @kitty-cmd DCS — sub-millisecond vs the ~30-100ms kitten subprocess
        spawn. The raw bytes are exactly what the kitten client sends
        (captured live): ESC P @kitty-cmd {json} ESC \\, with the reply (when
        requested) framed the same way. Speed is load-bearing for the mirror
        renderer AND the hook path: get-text runs on every click-to-view
        toggle, the tab paint runs on the BLOCKING hook path several times per
        turn, and the scroll runs INSIDE its DEC 2026 freeze bracket, where a
        subprocess outlives kitty's render-freeze window and exposes the
        intermediate frame (the toggle flicker). Returns the parsed response,
        True (fire-and-forget success), or None on any failure — callers
        fall back to the subprocess path."""
        listen = self.listen or ""
        path = listen[5:] if listen.startswith("unix:") else listen
        if not path:
            return None
        # Deferred: this is the only site, and every hook process imports this
        # module — a top-level socket import would be paid by all of them.
        import socket  # noqa: PLC0415

        obj = {
            "cmd": cmd,
            "version": KITTY_RC_VERSION,
            "no_response": not want_response,
            "payload": dataclasses.asdict(payload),
        }
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                s.settimeout(timeout)
                s.connect(path)
                s.sendall(RC_CMD_DCS + json.dumps(obj).encode("utf-8") + RC_ST)
                if not want_response:
                    return True
                buf = b""
                while RC_ST not in buf:
                    b = s.recv(65536)
                    if not b:
                        return None
                    buf += b
            finally:
                s.close()
            reply = buf[buf.index(RC_CMD_KEY) + len(RC_CMD_KEY) : buf.index(RC_ST)]
            return KittyRcResponse.model_validate_json(reply)
        except Exception:
            return None

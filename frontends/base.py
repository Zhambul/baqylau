# frontends/base.py — the Frontend interface every terminal adapter implements.
#
# The base class doubles as the inert "none" frontend: every operation is a
# silent no-op with the same failure-shaped return the callers already handle
# (rc 1 / [] / None), so code written against a Frontend never needs to check
# which terminal — or whether ANY terminal — is present. Frontends are
# best-effort and silent by contract, exactly like the old claude_kitty
# helpers: a failed call returns its failure value, never raises; the CALLER
# audits what matters.
#
# Window dicts: iter_windows()/find_window() yield plain dicts. Every frontend
# must provide at least  id, user_vars (dict), columns, lines  per window —
# richer terminal-specific keys (kitty's `neighbors`/`groups`) stay private to
# that frontend's own methods (e.g. split_geometry).
#
# The interface is deliberately ONE class, but consumers each use a narrow
# role slice (documented per section below) — a new frontend can be brought up
# slice by slice, testing one consumer at a time, and a slice it cannot
# support may simply keep the inert defaults (that consumer degrades to
# no-op, the others keep working). tests/test_l0_frontends_contract.py pins
# this substitutability: every public method has an inert default, and kitty
# adds no public API beyond the interface (except the documented `listen`/
# `kitten` constructor attrs, which nothing outside frontends/ may touch).


# Default inactive-tab foreground for set_tab_color — a soft grey every frontend
# shares (kitty imports this rather than re-encoding the hex).
INACTIVE_FG = "#c0c4cc"

# The kitty set-tab-color sentinel that clears a colour back to the theme
# default — clear_tab_color paints all four channels with it.
TAB_COLOR_NONE = "NONE"


class Frontend:
    name = "none"

    # --- presence ---------------------------------------------------------------
    # Slice consumers: everyone gates on this first — tabstatus (available/
    # usable/export_env), split + codex/session + adopt + scorebar (usable),
    # hostpane + split + codex/session (current_window as the pane anchor).
    def available(self):
        """True when the terminal's control channel is reachable in principle
        (env says we're inside this terminal). Cheap — no I/O."""
        return False

    def usable(self):
        """True when the control BINARY is also present (available() plus the
        client tool resolves). May stat the filesystem."""
        return False

    def current_window(self):
        """This process's own window id (from the terminal's env), or ""."""
        return ""

    def export_env(self):
        """Stamp whatever env detached children need to reach this terminal
        (kitty: KITTY_LISTEN_ON — streamers and the codex watcher inherit the
        hook's env and later shell out to the tab dispatcher)."""
        return None

    def app_id(self):
        """The OS-level application identifier of the terminal APP (macOS
        bundle id — kitty: net.kovidgoyal.kitty), or "" when unknown. Lets a
        consumer recognise "the terminal just became the frontmost app"
        without naming a specific terminal (the dashboard's passive
        steal watch after a web launch — audit-only, never moves focus)."""
        return ""

    # --- tab colour ---------------------------------------------------------
    # Slice consumers: plugins/claude_code/tabstatus.py only (the tab
    # dispatcher paints and clears; nothing else touches tab colour).
    def set_tab_color(self, win, active_bg, active_fg, inactive_bg,
                      inactive_fg=INACTIVE_FG):
        """Colour the tab containing window `win`. Returns an exit code
        (0 = the terminal acknowledged the paint; callers audit non-zero)."""
        return 1

    def clear_tab_color(self, win):
        """Revert the tab containing `win` to the theme default. Exit code."""
        return 1

    # --- window enumeration -------------------------------------------------
    # Slice consumers: hostpane (ls/find_window liveness probes), split
    # (iter_windows, window_for_session), tabstatus + codex/session +
    # scorebar (window_for_session), adopt (find_window to retag panes).
    def ls(self):
        """The raw OS-window/tab/window tree, [] on failure."""
        return []

    def iter_windows(self, tree=None):
        """Flatten ls() into (os_window, tab, window) triples."""
        return iter(())

    def find_window(self, var, value, tree=None):
        """First window dict whose user_vars[var] == value, or None."""
        for _o, _t, w in self.iter_windows(tree):
            if (w.get("user_vars") or {}).get(var) == value:
                return w
        return None

    def window_for_session(self, sid, tree=None):
        """Window id (str) of the pane tagged claude_session=<sid>, or None."""
        w = self.find_window("claude_session", sid, tree)
        return str(w.get("id")) if w else None

    # --- pane management ------------------------------------------------------
    # Slice consumers: core/hostpane.py (goto_splits_layout/launch_pane/
    # close_pane/resize_pane — the mirror+scorebar lifecycle), split +
    # codex/session + adopt (set_user_vars pane tagging), split (resize_pane
    # grow/shrink).
    def goto_splits_layout(self, win=None):
        """Switch a tab to a layout where directional splits with a size bias
        work (kitty: `goto-layout splits`) — the tab holding window `win` when
        given, else the active tab. Exit code."""
        return 1

    def launch_pane(self, argv, location, bias=None, var=None, title=None,
                    next_to=None, in_tab_of=None, cwd="current",
                    keep_focus=True):
        """Open a new pane running `argv`. location is "vsplit"/"hsplit";
        `var` is a {name: value} user-var tag dict; `next_to` is a raw window
        match string anchoring the split (e.g. "id:42",
        "var:claude_mirror=<sid>") — without it the terminal splits whatever
        window happens to be active. `in_tab_of` is a window id whose TAB the
        pane must open in — next_to alone cannot cross tabs (see
        frontends/kitty.py). Exit code."""
        return 1

    def close_pane(self, var=None, win_id=None):
        """Close the window matched by user-var (name, value) or id. Exit code."""
        return 1

    def set_user_vars(self, win_id, uv):
        """Set user-vars {name: value} on window `win_id`. Exit code."""
        return 1

    def resize_pane(self, var, axis, increment):
        """Resize the window matched by user-var (name, value) along
        "horizontal"/"vertical" by `increment` cells (negative shrinks)."""
        return 1

    # --- control plane (writes) ---------------------------------------------
    # Slice consumers: dashboard/server.py only (the web control plane —
    # docs/dashboard.md). These are the only Frontend methods that TYPE INTO or
    # SPAWN a terminal on someone's behalf, so they live behind the same strict
    # POST guard the dashboard applies; a frontend that can't drive input keeps
    # the inert False and the endpoint returns a clean "no terminal" error.
    def send_text(self, win, text):
        """Type `text` into window `win`, followed by Enter (a carriage
        return), as-is — no shell and no escape interpretation. True when the
        terminal acknowledged the write, else False."""
        return False

    def paste_text(self, win, text):
        """Like send_text, but deliver `text` as an ATOMIC bracketed paste
        (then Enter). A raw send_text is read as fast individual keystrokes
        and a TUI whose input just changed state (e.g. right after a cancel
        cleared its draft) drops the leading bytes; a bracketed paste is read
        whole (measured — docs/dashboard.md, the cancel-edit resend). True on
        success, else False."""
        return False

    def send_key(self, win, *keys):
        """Press key(s) into window `win` as KEY EVENTS (e.g. "escape",
        "ctrl+c") — encoded for the program's current keyboard mode, which
        raw send_text bytes would bypass (a TUI in the kitty keyboard
        protocol never sees a bare \\x1b as Escape). True when the terminal
        accepted the call, else False."""
        return False

    def launch_tab(self, cwd, argv):
        """Open a NEW tab whose window runs `argv` with working directory
        `cwd`. Truthy on success — the new window's id when the terminal
        reports one (kitty prints it; the dashboard matches the launched
        session by `kitty_window_id`), else bare True — and falsy (False/None)
        on failure. May select the new tab inside the terminal, but must NOT
        make the terminal APP take OS-level focus when it is in the background
        (the web dashboard's caller — its user is in a browser; kitty: a plain
        launch is safe, --keep-focus is the thing that activates a background
        app)."""
        return False

    def close_tab(self, win):
        """Close the whole tab CONTAINING window `win` (the session's main
        window + its mirror/scorebar panes). The session process gets SIGHUP
        and exits gracefully — Claude Code fires SessionEnd on it (verified
        2026-07-18, docs/dashboard.md), so the normal end-of-session lifecycle
        (mirror park, audit close) runs on its own. True on success."""
        return False

    def set_tab_title(self, win, title):
        """Explicitly title the tab CONTAINING window `win` (the web rename).
        STICKY in kitty: an explicit tab title stops following the active
        window's OSC title — Claude Code's auto ai-title — for the rest of the
        session, which is deliberate for a deliberately-named session
        (docs/session-naming-findings.md §4). True when the terminal accepted
        the call, else False."""
        return False

    # --- viewport scroll / read ---------------------------------------------
    # Slice consumers: claude-mirror.py only (the renderer's click-to-view
    # scroll restore + get_text scroll-position anchor). A frontend without
    # scroll control may leave these inert — the mirror still renders, only
    # the exact-scroll restore degrades.
    def scroll_window(self, win_id, lines_up):
        """Scroll window `win_id`'s viewport UP by `lines_up` lines (the
        renderer restoring a click-to-view line into view after a reflow)."""
        return 1

    def scroll_window_fast(self, win_id, lines_up):
        """Low-latency scroll_window (raw socket, no subprocess) for use
        inside a render-freeze bracket. True on success; False = caller falls
        back to scroll_window."""
        return False

    def scroll_window_end(self, win_id):
        """Scroll the viewport to the absolute bottom — the deterministic
        base for a relative restore scroll. False on failure."""
        return False

    def get_text(self, win_id, extent="screen"):
        """The window's VISIBLE text (the scrolled-to viewport, not the live
        screen), or None. The renderer's scroll-position anchor."""
        return None

    # --- geometry -------------------------------------------------------------
    # Slice consumers: plugins/claude_code/split.py only (setpct/reset and the
    # remembered pane size need the pane's share of its row).
    def split_geometry(self, var, exclude_var=None):
        """(pane_columns, row_total_columns) for the pane tagged by the
        user-var (name, value) pair, excluding panes carrying `exclude_var`
        from the totals; None when it can't be measured."""
        return None

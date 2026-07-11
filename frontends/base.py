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


class Frontend:
    name = "none"

    # --- presence -------------------------------------------------------------
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

    # --- tab colour -----------------------------------------------------------
    def set_tab_color(self, win, active_bg, active_fg, inactive_bg,
                      inactive_fg="#c0c4cc"):
        """Colour the tab containing window `win`. Returns an exit code
        (0 = the terminal acknowledged the paint; callers audit non-zero)."""
        return 1

    def clear_tab_color(self, win):
        """Revert the tab containing `win` to the theme default. Exit code."""
        return 1

    # --- window enumeration -----------------------------------------------------
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

    # --- pane management --------------------------------------------------------
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

    def resize_pane(self, var, axis, increment):
        """Resize the window matched by user-var (name, value) along
        "horizontal"/"vertical" by `increment` cells (negative shrinks)."""
        return 1

    def split_geometry(self, var, exclude_var=None):
        """(pane_columns, row_total_columns) for the pane tagged by the
        user-var (name, value) pair, excluding panes carrying `exclude_var`
        from the totals; None when it can't be measured."""
        return None

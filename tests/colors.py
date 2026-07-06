# colors.py — the tab colour contract (mirrors COLORS in claude-tab-status.py
# and the README colour table). This is the exact surface a terminal-
# abstraction layer must keep painting; L3 asserts the recorded backend argv
# against it.
COLOR_TABLE = {
    #                    active_bg   active_fg  inactive_bg
    "idle":              ("#5c6370", "#e6e9ef", "#33373f"),   # grey
    "thinking":          ("#c678dd", "#1a0620", "#4a2b52"),   # magenta (busy)
    "working":           ("#c678dd", "#1a0620", "#4a2b52"),
    "executing":         ("#61afef", "#06121f", "#2c4a63"),   # blue (running)
    "awaiting-bg":       ("#61afef", "#06121f", "#2c4a63"),
    "awaiting-command":  ("#e06c75", "#2a0608", "#5e2d31"),   # red (asking you)
    "awaiting-response": ("#98c379", "#07180a", "#445733"),   # green (your turn)
}
INACTIVE_FG = "#c0c4cc"


def tab_color_argv(listen, win, state):
    """The exact `kitten` argv claude_kitty.set_tab_color must produce."""
    bg, fg, ibg = COLOR_TABLE[state]
    return ["@", "--to", listen, "set-tab-color",
            "--match", "window_id:%s" % win,
            "active_bg=%s" % bg, "active_fg=%s" % fg,
            "inactive_bg=%s" % ibg, "inactive_fg=%s" % INACTIVE_FG]


def clear_argv(listen, win):
    return ["@", "--to", listen, "set-tab-color",
            "--match", "window_id:%s" % win,
            "active_bg=NONE", "active_fg=NONE",
            "inactive_bg=NONE", "inactive_fg=NONE"]

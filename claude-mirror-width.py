#!/usr/bin/env python3
# claude-mirror-width.py — read `kitten @ ls` JSON on stdin and print the column
# width of the command-mirror window (the one tagged user_var claude_mirror).
# Prints nothing if it can't be found; claude-cmd-log.sh then falls back to 53.
import json, sys

try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit()
for w in (win for osw in d for t in osw.get("tabs", []) for win in t.get("windows", [])):
    if (w.get("user_vars") or {}).get("claude_mirror"):
        print(w.get("columns") or "")
        break

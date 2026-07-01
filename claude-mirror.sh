#!/usr/bin/env bash
# claude-mirror.sh LOG
#
# Runs INSIDE the kitty "command mirror" split (opened by claude-split.sh) and
# execs the renderer on the given per-session log path. Producers (the *-fmt.py
# hooks + claude-stream.py / claude-substream.py) append width-INDEPENDENT paint
# ops (JSONL, keyed per session by claude_ops.log_path) to that log; claude-mirror.py
# reads them and paints at the pane's CURRENT width, re-rendering everything on
# resize (SIGWINCH) so the content reflows. Each command Claude runs appears as:
#
#   ▶ command            (highlighted, blue)
#   ──────────────
#   output
#   ──────────────
#   ■ finished · 1.2s    (highlighted, magenta)
#
# The renderer reads the log from the top (no truncation), so toggling the pane
# off/on re-shows the whole session's history; while off there is no process at all.

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
log="${1:-/tmp/claude-mirror-$(pwd -P | sed 's#[/.]#-#g').log}"

# The renderer syntax-highlights commands (bash + embedded python) with pygments,
# and does so IN THIS PROCESS — so the interpreter we exec must have pygments, or
# every command paints in the plain default colour with no highlighting at all.
# kitty launches this pane with a PATH whose `python3` is often the bare macOS /
# Xcode build (no pygments), so probe for an interpreter that can import it and
# fall back to plain `python3` (still runs, just uncoloured) so the mirror always
# starts. Set CLAUDE_MIRROR_PYTHON to force a specific interpreter.
pick_python() {
  local c versdir latest
  local cands=("$CLAUDE_MIRROR_PYTHON" python3
               "$HOME/.pyenv/shims/python3"
               /opt/homebrew/bin/python3 /usr/local/bin/python3)
  # Newest pyenv-installed CPython (e.g. .../versions/3.12.1/bin/python3), if any.
  if [ -d "$HOME/.pyenv/versions" ]; then
    latest="$(ls -1d "$HOME"/.pyenv/versions/[0-9]*/ 2>/dev/null | sort -V | tail -1)"
    [ -n "$latest" ] && cands+=("${latest}bin/python3")
  fi
  for c in "${cands[@]}"; do
    [ -n "$c" ] || continue
    if command -v "$c" >/dev/null 2>&1 && "$c" -c "import pygments" >/dev/null 2>&1; then
      command -v "$c"; return 0
    fi
  done
  echo python3
}

exec "$(pick_python)" "$here/claude-mirror.py" "$log"

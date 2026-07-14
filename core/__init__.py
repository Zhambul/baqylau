# core/ — the tool- and terminal-agnostic runtime of the mirror system.
#
# Everything here must stay importable with zero knowledge of WHICH agent tool
# (Claude Code, codex, …) produced an event or WHICH terminal (kitty, …) shows
# it. The dependency rule for the whole repo (see docs/architecture.md):
#
#   core/       imports nothing outside core/ (stdlib + optional pygments only)
#   frontends/  imports core/ at most
#   plugins/    import core/ and frontends/, never each other
#   claude-*.py entry scripts at the repo root are the assembly layer — they may
#               import anything, and their basenames are the audit vocabulary
#               (argv[0] is what `hook_events.handler` / `errors.script` record)

# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

**baqylau** — observability for agent coding sessions.
It watches sessions from several harnesses (Claude Code, Codex), interprets what they do
into one canonical event model, and presents it in a kitty terminal pane and a localhost
web dashboard.

## Commands

```sh
python3 bin/baqylau-audit.py session <sid>   # all raw evidence + its interpretations
python3 bin/baqylau-audit.py raw <raw_id>    # one observation, exact bytes

python3 bin/baqylau-dashboard.py serve|start|stop|status

make test        # full suite
make lint        # must stay clean (ruff, encodes docs/styleguide.md)
make lint-fix

pip3 install -r requirements.txt   # fastapi + uvicorn — the api/ layer's
                                   # runtime dependencies (the rest is stdlib)
```

To debug a session bug, use the **`audit-debug` skill**
(`.claude/skills/audit-debug/SKILL.md`) — it has the schema and the known bug shapes.

# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

**baqylau** — observability for agent coding sessions.
It watches sessions from several harnesses (Claude Code, Codex), interprets what they do
into one canonical event model, and presents it in a kitty terminal pane and a localhost
web dashboard.

## Commands

Two databases: `<data_dir>/main.db` holds everything the application owns and
reads back, `<data_dir>/audit.db` what the machinery did. There is no third:
the daemon is a singleton because it binds a port, not because it holds a lock.
**Nothing outside `repository/impl/sqlite/` opens a database** — the one
exception is `harness/impl/codex/canonical/title.py`, which is itself a
repository implementation and lives there only because a shared package may not
contain a harness's name.

```sh
python3 bin/baqylau-raw-events-audit.py session <sid>  # every raw event + interpretation
python3 bin/baqylau-raw-events-audit.py raw <raw_id>   # one raw event, exact bytes

python3 bin/baqylau-dashboard.py serve|start|stop|status

make test        # full suite
make lint        # must stay clean (ruff, encodes docs/styleguide.md)
make lint-fix

pip3 install -r requirements.txt   # fastapi + uvicorn — the api/ layer's
                                   # runtime dependencies (the rest is stdlib)
```

To debug a session bug, use the **`audit-debug` skill**
(`.claude/skills/audit-debug/SKILL.md`) — it has the schema and the known bug shapes.

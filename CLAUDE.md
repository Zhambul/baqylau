# CLAUDE.md
only use ASD-STE100 Simplified Technical English everywhere

Guidance for Claude Code working in this repository.

## What this is

**baqylau** — observability for agent coding sessions.
It watches sessions from several harnesses (Claude Code, Codex), interprets what they do
into one canonical event model, and presents it in a kitty terminal pane and a localhost
web dashboard.


To debug a session bug, use the **`audit-debug` skill**
(`.claude/skills/audit-debug/SKILL.md`) — it has the schema and the known bug shapes.

<Important! HARD RULES. read carefully and follow them!>
only use ASD-STE100 Simplified Technical English everywhere
We care about design and simplicity
Simplicity is not about simplest and quickest approach. Simplicity takes effort and redesign and refactoring and thinking about how in the future this code would be simple to read and to extend.
If you are adding a new feature which does break the simplicity a redesign and refactoring is allowed.
But it should be always asked from the user.
Always think about how to make a code better and simpler to read and to extend.
Do not just focus on the hacky quickest solution. Think about the future and how to make it better.
Comunicate with the user and ask for feedback. If you are not sure about a design or a solution, ask for help.
Do not overengineer. Do not add features which are not needed. Do not add features which are not asked for.
Do not reinvent the wheel. If a library or a package exists which does what you need, use it. Do not write your own implementation unless it is ix explicitly asked for.
</Important! HARD RULES. read carefully and follow them!>

## How restart a daemon/dashboard

The main dashboard is owned by its macOS LaunchAgent. Do not use `stop` and
`start` because the LaunchAgent can race the manual process.

```sh
make build-frontend
launchctl kickstart -k gui/$(id -u)/top.zhambyl.baqylau-dashboard
python3 bin/baqylau-dashboard.py status --port 8377
```

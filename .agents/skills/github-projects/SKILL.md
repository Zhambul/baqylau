---
name: github-projects
description: Manage the Baqylau GitHub Project when the user asks to create, update, move, prioritize, comment on, close, or filter project work. Do not use for private planning that the user did not ask to publish to GitHub.
---

# GitHub Projects

Use the project SDK for every board operation:

```sh
python3 .agents/skills/github-projects/scripts/github_projects_sdk.py --help
```

It manages `Zhambul/baqylau` and GitHub Project 1. Authentication comes from
`GITHUB_TOKEN`, `GH_TOKEN`, or the active `gh` login. Never print a token.

- Read before a write and do not create duplicate work.
- Creation requires one `Area`, one `Work Type`, and one `Priority`; the initial status is `Backlog`.
- Move active work to `In Progress`. Move it to `Done` only after checks pass.
- Add comments only for useful results, decisions, or blockers.
- Make only the external changes that the user asks for.
- Creating a Backlog issue automatically re-sorts the backlog.
- Run `github_projects_sdk.py sort-backlog --apply` after changing the priority of an existing Backlog issue.

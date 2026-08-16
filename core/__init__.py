"""The floor: utilities that know the operating system, not the domain.

    env.py         typed environment reads
    process.py     process liveness and ancestry
    locks.py       pid-liveness locks over a caller-supplied database
    data.py        where our data lives
    repository.py  git worktree facts about a directory
    daemon/        the daemon's HTTP door — the contract and the client

Nothing here knows what a session, a harness, or a terminal is. Operational
diagnostics used to live here too; they own their own database, so they own
their own directory (`diagnostics/`).
"""

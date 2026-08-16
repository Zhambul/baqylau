"""Evidence becomes fact here, and the world hears about it.

    loop.py         the one interpreter thread: pull, translate, react
    liveness.py     the source it builds itself — the CLI process is gone
    translators.py  translators for the evidence our OWN machinery produces
    reactions.py    the core reactions to committed facts, one concern each

The only tier that both reads and writes. Everything below it appends; every
surface above it only reads.
"""

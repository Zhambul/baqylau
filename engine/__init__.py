"""The neutral middle: evidence in, facts out, read models over the facts.

    store/        where facts live — one database, one owner per table
    interpret/    the one thread that pulls evidence, translates, and reacts
    projections/  semantic read models folded from committed facts
    queries/      reads that need nothing but the facts

Harness-neutral and terminal-neutral throughout: it names the harness CONTRACT,
because it drives plugins it is handed, and never a concrete one. Nothing here
knows there is a daemon, an HTTP door, or a browser.
"""

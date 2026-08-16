"""The composition root: where the concrete parts are chosen and wired.

    bootstrap.py     builds the one application graph, once, in the daemon
    services/        services that compose concerns the engine keeps apart
    evidence_cli.py  the audit CLI — the one sanctioned reader outside the daemon

The only package that knows which harnesses and which terminal are installed.
"""

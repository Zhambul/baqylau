"""The composition root: where the concrete parts are chosen and wired.

    providers.py     every node of the application, one provider each
    injection.py     the injection kernel: singleton scope, and resolving one
    services/        services that compose concerns the engine keeps apart
    evidence_cli.py  the audit CLI — the one sanctioned reader outside the daemon

The only package that knows which harnesses and which terminal are installed.
"""

"""Services that compose concerns the engine deliberately knows nothing about.

    insights.py  cross-session rollups: canonical facts + git + diagnostics
    resume.py    what the resume picker may offer: facts + the repositories

Each one reaches across a boundary the engine keeps closed, which is exactly
why it lives up here with the composition root rather than down there.
"""

"""The step implementations, in sections.

One module per concern, because 800 lines of steps in one file was a bucket:
`session` starts one and reads what it reports, `messages` says things to it and
reads what came back, `shells` covers commands and the two ways their output
outlives a turn, `subagents` covers delegation, and `world` holds the state a
scenario carries plus the reads every section makes.

conftest.py pulls these into its own namespace, which is not a style choice —
pytest-bdd registers each step as a FIXTURE in the module that defines it, and
pytest only discovers fixtures from conftest and test modules.
"""

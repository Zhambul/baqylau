"""The application tier over a resolved harness plugin — one service per concern.

Each service takes the registry (or the session store) and turns an application
request into a call on ONE plugin field: `controls` dispatches gestures at its
controller, `launcher` prepares and opens a tab, `catalog` reads its menus,
`usage` collects its plan limits, `probe` reads its live input line. Nothing
here knows a concrete harness — that is `harness/impl/`'s business.
"""

"""Terminal-side policy, above the storage and below the routes.

    panes.py  the activity pane's width: what is remembered, what is configured
    views.py  which content views the mirror has expanded

These exist because a route and a renderer were each reaching a storage module
directly, and because the "stored width, else the configured default"
resolution was spelled twice inside the store it read from.
"""

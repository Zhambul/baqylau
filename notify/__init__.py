"""Alerts about sessions: when one is owed you, where it should reach you.

    notifier.py  WHEN — the tab-state diff, the grace window, the arm/cancel/
                 escalate machine, and the one writer of the alert audit rows
    presence.py  WHETHER — the live signals that say you do not need alerting:
                 a browser is looking, a device is in your hand, a reply is
                 half-typed
    channels/    WHERE — one module per destination, each owning its own send,
                 its own retraction, and the handle that ties them together

An alert is a promise the session makes to you when you are not watching, so
every send here is retractable: the moment the reason evaporates, the banner
comes back down.
"""

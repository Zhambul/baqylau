#!/usr/bin/env python3
"""The suite's stand-in for the `notify` skill script (docs/testing.md,
*Hermeticity*).

`dashboard/notify/channels.py` degrades to spawning `config.NOTIFY_CMD` — by
default the developer's REAL `~/.claude/skills/notify/scripts/notify.py`, i.e.
their actual Telegram bot — whenever the Bot API credentials are unconfigured.
The hermetic fixture makes them unconfigured ON PURPOSE, so every test that
drives a red/green transition through an un-stubbed `Notifier` used to deliver a
real alert to the developer's phone. conftest aims the knob here instead.

Deliberately a no-op: the point is that the argv never leaves the machine. Set
BAQYLAU_NOTIFY_SINK_LOG to keep a record when debugging what the suite would
have sent.
"""
import os
import sys


def main():
    log = os.environ.get("BAQYLAU_NOTIFY_SINK_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write("\t".join(sys.argv[1:]).replace("\n", "\\n") + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

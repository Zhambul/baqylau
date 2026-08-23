# api/lifecycle.py — what the daemon runs BESIDE the request loop, owned by the
# ASGI lifespan instead of by hand around uvicorn.
#
# The three threads and the one boot chore used to live in the server's
# try/finally block.
# They live here because they need exactly what the routes need — the same
# interpreter, the same usage state — and the singleton registry is the app's, so
# the thing that starts them has to run inside the application's own lifetime.
from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import contextmanager
from typing import Iterator

from app import providers
from app.injection import Instances, resolve
from notify.notifier import Notifier


def _notifier(instances: Instances) -> Notifier:
    return Notifier(
        resolve(instances, providers.session_data),
        resolve(instances, providers.terminal),
        resolve(instances, providers.repositories),
        resolve(instances, providers.dashboard_notification_state),
        resolve(instances, providers.notification_settings),
        resolve(instances, providers.push_subscriptions),
        resolve(instances, providers.push_signing_keys),
        resolve(instances, providers.presence),
        resolve(instances, providers.recorder),
    )


def _worker(name: str, run: Callable[[threading.Event], None],
            stop: threading.Event) -> threading.Thread:
    thread = threading.Thread(target=run, args=(stop,), daemon=True, name=name)
    thread.start()
    return thread


@contextmanager
def background_workers(instances: Instances) -> Iterator[None]:
    """The daemon's non-request work, started on entry and stopped on exit.

    Resolved from the same providers the routes resolve from, so a worker and a
    route hold ONE interpreter, ONE usage state, ONE session list cache.
    """
    interpreter = resolve(instances, providers.interpreter)
    reactions = resolve(instances, providers.reaction_loop)
    usage_state = resolve(instances, providers.usage_state)
    # Attachments are pruned from the ROW, not by walking the directory and
    # trusting mtimes: what we wrote is what we know about.
    resolve(instances, providers.uploads).prune()

    observation_stop = threading.Event()
    reaction_stop = threading.Event()
    usage_stop = threading.Event()
    notifier_stop = threading.Event()
    observation = _worker("baqylau-interpreter", interpreter.run, observation_stop)
    # The second loop of the two: the interpreter appends facts, this follows
    # them. Decoupled by the canonical cursor, so neither waits on the other.
    reaction = _worker("baqylau-reactions", reactions.run, reaction_stop)
    usage = _worker("baqylau-usage", usage_state.run, usage_stop)
    # The notifier used to be the one worker with no stop event and no join: it
    # died with the process. It stops like its siblings now.
    notifier = _worker("baqylau-notifier", _notifier(instances).run, notifier_stop)
    try:
        yield
    finally:
        observation_stop.set()
        reaction_stop.set()
        usage_stop.set()
        notifier_stop.set()
        observation.join(timeout=2)
        reaction.join(timeout=2)
        usage.join(timeout=2)
        notifier.join(timeout=2)

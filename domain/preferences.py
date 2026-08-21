"""What YOU chose: cross-session, cross-device, and true only for you.

Nothing here is a fact about a session — those are folded from raw events and are
the same for everyone. These are yours, and a fresh machine simply has none of
them.

Each of these was a JSON blob under a key in one `kv` table, validated by hand
at every read. They are types now, and each has a table with a real primary key.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from domain.ids import DeviceId


# Which of the harness TUI's three native history densities a session's web
# mirror is rendered at, in CONTROL order — the segmented control reads densest
# to sparsest, which is why the default is not the first entry.
ViewMode: TypeAlias = Literal["verbose", "default", "focus"]
DEFAULT_VIEW_MODE: ViewMode = "default"


@dataclass(frozen=True)
class HiddenDirectory:
    """A project directory hidden from the list page, and when.

    Non-destructive: nothing is closed, the group just disappears from view. It
    re-appears the moment a session STARTED after `hidden_at` shows up in it —
    but that comparison is client-side; this only holds the stamp.
    """

    working_directory: str
    hidden_at: float


@dataclass(frozen=True)
class NewSessionPreferences:
    """The launch form's last picks."""

    working_directory: str | None
    harness: str | None
    model: str | None
    effort: str | None


@dataclass(frozen=True)
class NewSessionDraft:
    """One directory's half-typed first prompt.

    Per-directory, because different projects hold different half-typed
    prompts. `sequence` is the writer's wall clock and is the stale-write guard:
    a debounced save in flight when the launch clears the box must not
    resurrect it by landing later. A clear is a TOMBSTONE (empty text at the
    newer sequence), never a delete, so its sequence survives to reject that
    straggler.
    """

    working_directory: str
    text: str
    sequence: float


@dataclass(frozen=True)
class DraftWrite:
    """What saving a draft did: the stored entry, and whether this write lost."""

    draft: NewSessionDraft
    stale: bool


@dataclass(frozen=True)
class PushSubscription:
    """One browser that opted into Web Push.

    Keyed by endpoint so a re-subscribe from the same browser upserts in place
    instead of piling up duplicates. The device id and label ride along so the
    notifier can route to the most recently used device.
    """

    endpoint: str
    public_key: str
    authentication_secret: str
    device_id: DeviceId
    device_label: str | None
    created_at: float


@dataclass(frozen=True)
class PushSigningKeypair:
    """The VAPID keypair this installation signs its pushes with."""

    private_key_pem: str
    public_key: str

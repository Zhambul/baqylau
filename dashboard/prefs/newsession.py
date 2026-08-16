"""The new-session form: what you picked last time, and what you half-typed."""


from dashboard.prefs.store import mutate_map, stored_object


# --- the new-session form's unsent first prompts (one per directory) -------------
# The launch form's first-prompt box is a DRAFT like the composer's (docs/
# dashboard.md, *New-session draft*): closing the form — deliberately, with Esc,
# or by a stray click on the backdrop — must not throw the text away, and the
# next open restores it. Stored under one kv key as a PER-DIRECTORY map,
# {cwd: {text, sequence}} — different projects hold different half-typed prompts, and
# switching the form's directory switches which one is in the box (the single
# shared draft this started as bled one project's prompt into the next).
#
# The cwd KEY is whatever the page sends (`app.09-newsession.js` nsDirKey — the
# form's own notion of "the same folder": trimmed, trailing slashes dropped),
# stored verbatim here; the server is a dumb kv for it deliberately, so the
# normalization has ONE implementation instead of two that can disagree. "" is a
# legitimate key (the form opened with no directory yet).
#
# `sequence` is the writer's wall clock, same STALE-WRITE GUARD as the composer draft
# (api/dashboard/application.py save_composer_draft), applied PER ENTRY: a debounced
# save in flight when the launch clears the box must not resurrect it by landing
# later. A clear is a TOMBSTONE (empty text at the newer sequence), never a delete, so
# its sequence survives to reject that straggler.
#
# The map is PRUNED to the NS_DRAFT_MAX most recent entries by sequence (tombstones
# included — recency, not emptiness, is what decides): the form is opened against
# a handful of projects in practice, and an unbounded map would accumulate a row
# per directory ever typed into, forever.
NEW_SESSION_DRAFT_KEY = "new-session-draft"
NEW_SESSION_DRAFT_LIMIT = 24


def _new_session_draft(document):
    """Validate and return one stored {text, sequence} draft."""
    if not isinstance(document, dict):
        raise TypeError("new-session draft must contain an object")
    text = document.get("text")
    sequence = document.get("sequence")
    if not isinstance(text, str):
        raise TypeError("new-session draft text must be a string")
    if not isinstance(sequence, (int, float)) or isinstance(sequence, bool):
        raise TypeError("new-session draft sequence must be a number")
    return {"text": text, "sequence": sequence}


def new_session_drafts():
    """Every unsent new-session prompt as {cwd: {text, sequence}} ({} when none /
    unreadable). The page caches this whole map so opening the form seeds the
    box synchronously — it is bounded by NS_DRAFT_MAX."""
    document = stored_object(NEW_SESSION_DRAFT_KEY)
    return {
        str(working_directory): _new_session_draft(draft)
        for working_directory, draft in document.items()
    }


def set_new_session_draft(working_directory, text, sequence):
    """Persist `text` at `sequence` as the draft for directory `cwd`, DROPPING a write
    older than that directory's stored sequence (the stale-write guard above — per
    entry, so two directories' saves never fight) and pruning the map back to
    NS_DRAFT_MAX. Atomic read-modify-write (mutate_map — one BEGIN IMMEDIATE, so
    the compare, the set and the prune can't straddle a peer request thread's
    write). Returns the stored entry, with `stale` True when this write was
    rejected; best-effort like set()."""
    key = str(working_directory)
    keep = {}

    def _apply(d):
        current = (
            _new_session_draft(d[key])
            if key in d
            else {"text": "", "sequence": 0}
        )
        if sequence < current["sequence"]:
            keep["stale"] = True
            return
        d[key] = {"text": text, "sequence": sequence}
        if len(d) > NEW_SESSION_DRAFT_LIMIT:
            # oldest-first by sequence, keeping this write (it has the newest clock)
            for k in sorted(
                d,
                key=lambda draft_key: _new_session_draft(d[draft_key])["sequence"],
            )[:len(d) - NEW_SESSION_DRAFT_LIMIT]:
                d.pop(k, None)
    record = mutate_map(NEW_SESSION_DRAFT_KEY, _apply)
    return dict(_new_session_draft(record.get(key)), stale=bool(keep.get("stale")))

"""Where facts live: one interface between the application and its storage.

    contract/   the Protocols — the only thing a caller outside imports
    model/      row DTOs, one per table, the persistence shape
    mapper/     row DTO <-> model object, pure functions
    impl/       the concrete backend; SQLite today

Two databases and one owner per table. `main.db` holds everything the
application owns and reads back:

    sessions                  the interpreter's session-upsert reaction
    raw_events                any recorder process
    interpretations           the interpreter
    canonical_events          the interpreter
    interpretation_events      the interpreter
    operation_output          the interpreter's output reactions
    session_workspaces + composer_queue_items
      + dialog_answers + dialog_answer_selections
                              the session workspace repository
    notification_settings · session_notification_mutes · session_view_modes
      · hidden_directories · new_session_preferences · new_session_drafts
      · task_dismissals · push_subscriptions · push_signing_keys
                              one preference repository each
    pane_widths · opened_views
                              the terminal repositories
    account_usage_snapshots + account_usage_windows
                              the usage repository
    uploads                   the upload repository

`audit.db` holds what the MACHINERY did, and is separate because audit writes
must never break application work and because it is what you read when
`main.db` is the suspect. There is no third file: the daemon's pid claim used
to live in `locks.db`, and the port it binds answers that question already.

Every write in the system passes through here; nothing here interprets.
"""

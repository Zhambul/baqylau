# plugins/claude_code/accounting.py — Claude-Code token/cost accounting.
#
# The transcript parsing (Anthropic usage dicts, one-line-per-content-block
# dedup by message.id) and the model PRICE TABLE are Claude-Code knowledge, so
# they live in this plugin; the counters they feed (tokens/cost/tk_*) and the
# scoreboard rendering stay in core/ops.py. Another agent-tool plugin brings
# its own transcript format + price table and bumps the same core counters.
import json, os, time

try:
    from core import audit as A         # always-on audit trail (CLAUDE_AUDIT=0 disables)
except Exception:                       # audit must never break a producer
    class _NoAudit:
        def __getattr__(self, _):
            return lambda *a, **k: None
    A = _NoAudit()
from core import state as S


# Approximate per-MTok (input, output) USD for the resolved model — for the "≈ $X" cost
# estimate. First substring match wins, so order specific → general. Verified against
# the published price list (2026-07): Fable/Mythos 10/50 · Opus 4.6-4.8 5/25 · Sonnet
# 3/15 · Haiku 4.5 1/5 · legacy Opus 4.1/4.0/3 15/75. Cache reads bill 0.1× input;
# cache writes bill by TTL — 5-minute 1.25×, 1-hour 2× (usage carries the split in
# cache_creation.ephemeral_{5m,1h}_input_tokens; pricing everything at 1.25× under-
# counted a session whose writes were all 1h — cost_usd handles both). An unknown
# model shows no cost (cost_usd → None) rather than guess. No long-context premium:
# the 1M window bills at these flat rates (confirmed on the published price page).
#
# Sonnet 5 has an introductory 2/10 rate through 2026-08-31; the entry is picked at
# import time (hook processes are short-lived, so this is per-event in practice) and
# reverts to the 3/15 sticker automatically after the intro window.
SONNET5_INTRO_UNTIL = 1788220800   # 2026-08-31T00:00:00Z — end of the 2/10 intro rate
_SONNET5 = ("sonnet-5", 2.0, 10.0) if time.time() < SONNET5_INTRO_UNTIL else ("sonnet-5", 3.0, 15.0)
PRICES = (
    ("haiku",     1.0,  5.0),
    ("fable",    10.0, 50.0),
    ("mythos",   10.0, 50.0),
    _SONNET5,
    ("sonnet",    3.0, 15.0),
    # Keys are SUBSTRINGS of real model ids (cost_usd matches `key in model`).
    # The legacy entries must match the ids Anthropic actually ships:
    # claude-opus-4-1-20250805 / claude-opus-4-20250514 / claude-3-opus-20240229.
    # ("opus-4-0" and "opus-3" appear in NO real id — every legacy Opus run fell
    # through to the generic 5/25 row, a 3× cost undercount.) Order matters:
    # "opus-4-1" before "opus-4-2025", and the generic "opus" last so Opus 4.5+
    # (claude-opus-4-5/-4-8, 5/25 sticker) still lands there.
    ("opus-4-1",    15.0, 75.0),
    ("opus-4-2025", 15.0, 75.0),   # the dated Opus 4.0 id, claude-opus-4-20250514
    ("3-opus",      15.0, 75.0),   # Claude 3 Opus, claude-3-opus-20240229
    ("opus",         5.0, 25.0),
)


def cost_usd(model, tot_in, tot_out, tot_cache=0, tot_create=0, tot_create_1h=0):
    """Approximate USD for a run's token totals, or None for an empty/unknown model.
    tot_in = fresh billed input (input + cache_creation, priced at the input rate),
    tot_out = generated, tot_cache = cache_read (0.1× input), tot_create = the
    cache_creation share of tot_in — billed at 1.25× input, so it adds the +0.25×
    premium on top of the flat rate tot_in already paid. tot_create_1h = the
    1-hour-TTL share of tot_create, billed at 2× input — another +0.75× on top of
    the +0.25× every creation token already added (0 keeps the old all-5m math,
    so callers without the split are unchanged). See PRICES."""
    m = (model or "").lower()
    if not m:
        return None
    for key, pin, pout in PRICES:
        if key in m:
            return (tot_in * pin + tot_create * pin * 0.25
                    + tot_create_1h * pin * 0.75
                    + tot_cache * pin * 0.1 + tot_out * pout) / 1_000_000
    return None


def usage_fields(u):
    """(fin, out, cache_read, cache_create, create_1h) ints from an assistant
    message's usage dict — fin is the fresh billed input (input + cache_creation),
    the argument order cost_usd takes. create_1h is the 1-hour-TTL share of
    cache_create, read from the usage's cache_creation.ephemeral_1h_input_tokens
    breakdown (0 when the breakdown is absent — old transcripts price as all-5m,
    exactly what they did before the split existed)."""
    create = int(u.get("cache_creation_input_tokens") or 0)
    fin = int(u.get("input_tokens") or 0) + create
    out = int(u.get("output_tokens") or 0)
    cr = int(u.get("cache_read_input_tokens") or 0)
    cc = u.get("cache_creation")
    c1h = int(cc.get("ephemeral_1h_input_tokens") or 0) if isinstance(cc, dict) else 0
    return fin, out, cr, create, min(c1h, create)


def usage_fold(mid, fields, prev):
    """THE per-message.id dedup fold (the ~2.2x scoreboard-inflation fix): one
    assistant MESSAGE is written as one transcript line PER CONTENT BLOCK, each line
    repeating that message's usage with output_tokens a growing snapshot — so usage
    counts once per message id, and later lines of the SAME id add only the
    (clamped non-negative) per-field delta. Both accountants — bump_transcript here
    (main session) and claude-substream.py (agents) — must share this one
    implementation; they drifted apart once and the fix had to be made twice.

    `fields` is usage_fields(); `prev` is the carried record {"id", "f": [5 ints,
    the usage_fields order]} (or None). Returns (delta_fields, new_prev). Since
    cost_usd is linear, pricing the deltas equals the full-minus-previous cost, so
    callers price delta_fields directly. A carry persisted by a 4-field build
    (pre-create_1h) is padded with zeros — its straddling message counts the full
    1h share once, a one-time cents-scale premium over-count, never of tokens."""
    if prev and mid and prev.get("id") == mid:
        pf = list(prev.get("f") or [])
        pf += [0] * (len(fields) - len(pf))
        deltas = tuple(max(v - int(p or 0), 0) for v, p in zip(fields, pf))
    else:
        deltas = fields
    return deltas, ({"id": mid, "f": list(fields)} if mid else prev)


def fold_usage(path, pos=0, usage_last=None):
    """Fold an agent transcript's assistant-message token usage from byte offset
    `pos` to the last COMPLETE line, deduped by message.id (via usage_fold, carry
    `usage_last`). Returns (fin, out, cache_read, cache_create, create_1h,
    usage_last, consumed) — the five totals in cost_usd's argument order, the
    updated carry, and the byte offset consumed. Best-effort: zeros + unchanged
    cursor on any read error or partial-only tail.

    This is the batch analogue of claude-substream.py's inline per-line fold: it
    lets a crashed/killed streamer's un-bumped tail be reconciled at SubagentStop
    against the transcript's TRUE total (claude-subagent-fmt.py). Unlike
    bump_transcript, it does NOT skip isSidechain lines — an agent's own transcript
    IS its (sidechain) turns, exactly what the streamer folds."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return 0, 0, 0, 0, 0, usage_last, pos
    if size <= pos:                         # nothing new (or rotated shorter — don't guess)
        return 0, 0, 0, 0, 0, usage_last, pos
    try:
        with open(path, "rb") as f:
            f.seek(pos)
            chunk = f.read(size - pos)
    except OSError:
        return 0, 0, 0, 0, 0, usage_last, pos
    end = chunk.rfind(b"\n")
    if end < 0:                             # no complete line yet
        return 0, 0, 0, 0, 0, usage_last, pos
    ti = to = tc = tcr = t1h = 0
    for ln in chunk[:end].split(b"\n"):
        try:
            o = json.loads(ln)
        except Exception:
            continue
        if not isinstance(o, dict) or o.get("type") != "assistant":
            continue
        u = (o.get("message") or {}).get("usage")
        if not isinstance(u, dict):
            continue
        d, usage_last = usage_fold((o.get("message") or {}).get("id"),
                                   usage_fields(u), usage_last)
        ti += d[0]; to += d[1]; tc += d[2]; tcr += d[3]; t1h += d[4]
    return ti, to, tc, tcr, t1h, usage_last, pos + end + 1


def bump_transcript(log, transcript):
    """Fold the MAIN session's own token spend into the scoreboard state.

    Agents/codex bump their spend when their streamer finishes, but the main session
    has no streamer of its own — without this, the scoreboard's tokens/cost only move
    when an agent run ends (they'd sit "stuck" through plain main-session work). Hooks
    call this with the payload's transcript_path: it reads the session JSONL forward
    from the last position (the 'txpos' counter), sums each new assistant turn's
    usage into 'tokens' (fresh billed input + output — cache reads are replay, not
    spend) and 'cost' (cost_usd on that turn's model, cache read/write rates
    included), and advances the cursor. The whole read-modify runs inside ONE
    BEGIN IMMEDIATE transaction on the state DB (was an flock on the JSON sidecar),
    so concurrent hooks never double-count a turn. Sidechain (subagent) records are
    skipped — their own streamer already bumps them.

    One assistant MESSAGE is written as one JSONL line PER CONTENT BLOCK, each line
    repeating that message's usage (input/cache fields identical, output_tokens a
    growing snapshot — the last line has the final count). So usage is counted once
    per message.id, from its last line (usage_fold, carried across calls in the
    state's 'txlast' record). The read-modify-write of the cursor runs inside ONE
    BEGIN IMMEDIATE transaction, owned by claude_state.transcript_fold — this
    function only parses and prices. Best-effort: any failure rolls the
    transaction back and leaves the state unchanged."""
    if not log or not transcript:
        return {}
    moved = {}                              # what fold counted, for the audit below

    def fold(pos, prev):
        try:
            size = os.path.getsize(transcript)
        except OSError:
            return None
        if size < pos:                      # transcript rotated/replaced — restart
            pos = 0
            prev = None                     # ids from the old file mustn't dedup the new one
        if size <= pos:
            return None
        try:
            with open(transcript, "rb") as tf:
                tf.seek(pos)
                chunk = tf.read(size - pos)
        except OSError:
            return None
        end = chunk.rfind(b"\n")
        if end < 0:                         # no complete new line yet — keep cursor
            return None
        tok, usd = 0, 0.0
        # Per-category token split for the scoreboard's Σ breakdown row, from the
        # SAME usage_fields cost_usd prices: input (fresh, EXCL. cache creation —
        # fields[0] is input+create, so subtract fields[3]), output, cache read
        # (replay), cache write (creation). tk_in+tk_create == the billed 'fin', so
        # tk_in+tk_out+tk_create == the ▪-row 'tokens'; +tk_read is the extra the Σ
        # total carries (why it dwarfs the ▪ headline).
        cin = cout = cread = ccreate = c1h = 0
        rows = {}                           # message id -> last usage line seen for it
        for ln in chunk[:end].split(b"\n"):
            try:
                o = json.loads(ln)
            except Exception:
                continue
            if not isinstance(o, dict) or o.get("type") != "assistant" or o.get("isSidechain"):
                continue
            m = o.get("message") or {}
            u = m.get("usage") if isinstance(m, dict) else None
            if not isinstance(u, dict):
                continue
            fields = usage_fields(u)
            mid = m.get("id")
            if not mid:                     # no id to dedup on — count the line as-is
                tok += fields[0] + fields[1]
                usd += cost_usd(m.get("model"), *fields) or 0.0
                cin += fields[0] - fields[3]; cout += fields[1]
                cread += fields[2]; ccreate += fields[3]; c1h += fields[4]
                continue
            rows[mid] = (m.get("model"), fields)
        for mid, (model, fields) in rows.items():
            if prev and mid == prev.get("id") and "f" not in prev:
                # txlast persisted by a pre-usage_fold build ({"id","tok","usd"}):
                # delta the whole-message totals once, then re-persist as {"id","f"}.
                d_t = (fields[0] + fields[1]) - int(prev.get("tok") or 0)
                d_c = (cost_usd(model, *fields) or 0.0) - float(prev.get("usd") or 0.0)
                tok += max(d_t, 0)
                usd += max(d_c, 0.0)
                # legacy carry has no per-field split — count this one straddling
                # message's categories in full (a one-time small Σ-row over-count,
                # never of billed tok/usd), then re-persist in the {"id","f"} shape.
                cin += fields[0] - fields[3]; cout += fields[1]
                cread += fields[2]; ccreate += fields[3]; c1h += fields[4]
                prev = {"id": mid, "f": list(fields)}
                continue
            d, prev = usage_fold(mid, fields, prev)
            tok += d[0] + d[1]
            usd += cost_usd(model, *d) or 0.0
            cin += d[0] - d[3]; cout += d[1]
            cread += d[2]; ccreate += d[3]; c1h += d[4]
        # c1h (the 1h-TTL share of ccreate) prices at 2× instead of 1.25× — it rides
        # the audit row (re-pricing evidence), not comps: it is a pricing input, not
        # a fifth Σ-row display category, so no tk_create_1h counter exists.
        comps = {"tk_in": cin, "tk_out": cout, "tk_read": cread, "tk_create": ccreate}
        moved.update(tok=tok, usd=usd, txpos=pos + end + 1, txlast=prev,
                     comps=comps, c1h=c1h)
        return pos + end + 1, prev, tok, usd, comps

    try:
        st = S.transcript_fold(log, fold)
        # Audit only when spend actually moved (this is called on every hook; a
        # no-new-turns call is noise). Records the delta, the cursor advance, and
        # the resulting totals — the trail a token/cost inflation bug needs.
        if moved.get("tok") or moved.get("usd"):
            A.state_file(log, S.db_path(log), "bump-transcript", {
                "d_tokens": moved["tok"], "d_cost": round(moved["usd"], 6),
                "d_split": moved.get("comps"), "d_create_1h": moved.get("c1h"),
                "txpos": moved["txpos"], "txlast": moved["txlast"],
                "now": {"tokens": st.get("tokens"), "cost": st.get("cost")}})
        return st
    except Exception:
        A.error(log, "bump_transcript", {"transcript": transcript})
        return {}



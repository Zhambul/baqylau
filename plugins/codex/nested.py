# plugins/codex/nested.py — a session's CODEX RUNS as agent rows (plugins.runs).
#
# Named `nested`, not `runs`, for exactly the reason hostctl.py is not `host`:
# the `runs` PROVIDER function in plugins/codex/__init__.py SHADOWS a submodule
# of that name (a package attribute defined in __init__ wins over a same-named
# submodule for `from plugins.codex import runs`), so the provider imported
# ITSELF and every agents() call raised. Measured, not theorised.
#
# A codex run streams into a hosting session's mirror like a child agent does,
# so the read model lists it beside the Claude subagents/teammates: same row
# shape, `kind` "codex". Building those rows used to be `sessionapi.codex_runs`
# — a function NAMED after one tool, living in the tool-agnostic core, reading a
# stream kind only this plugin ever writes and applying a drop rule only this
# plugin's standalone mode can trigger. It is all one plugin's knowledge, so it
# lives with the plugin, behind the generic `plugins.runs(sid)` fan-out that
# `sessionapi.agents()` splices. A host with no nested-run concept simply has no
# provider — which is why claude_code declines it: a Claude subagent is not a
# "run", it is already an audit `streams` row of kind subagent/teammate that
# agents() reads directly.
#
# core keeps only the audit-query skeleton these rows are built from
# (`sessionapi.streams_by` — the chain→in-clause→select→merge walk every reader
# of the `streams` keystone shares) and the composition.
from core import paths as P
from core import sessionapi as API


def session_runs(sid):
    """The session's codex runs, chain-aware, from the audit streams keystone
    (kind='codex' — written by the codex tailer's stream_lifecycle) in the
    agents() row shape: agent_id is paths.codex_aid(src_path), desc is the run
    label (the streams task_id: 'cli', 'Review', …), transcript is the run's
    SOURCE file — a native rollout .jsonl (parseable by this plugin's read
    providers) or a companion job .log (activity log only; no drill-down).
    A restarted run (several stream rows, one src) merges like a restarted
    teammate: first start, newest end/status."""
    def fold(out, aid, row):
        src, task, st, en, er, lines = row
        rec = out.setdefault(aid, {"agent_id": aid, "kind": "codex",
                                   "transcript": src or "", "started_at": st,
                                   "desc": task or ""})
        rec["ended_at"], rec["end_reason"] = en, er or ""
        rec["tools"] = lines
    out = API.streams_by(sid, ("codex",),
                         "src_path, task_id, started_at, ended_at, end_reason,"
                         " lines_emitted",
                         lambda r: P.codex_aid(r[0]), fold)
    # Drop the STANDALONE host's OWN run. A codex running on its own writes its
    # session transcript AS a rollout (uuid == sid), and the standalone watcher
    # streams that very rollout under kind='codex' — so it lands here as a "run".
    # But it is the SESSION itself, not a nested sidecar: a standalone run's ops
    # are UNSTAMPED (codex is the main agent there), so listing it as an agent
    # mints a clickable card whose scope — {codex:<label>} — matches no op, and
    # clicking it yields an EMPTY mirror (the self-run empty-scope bug,
    # docs/codex.md). Its rollout IS the session's own transcript, which is the
    # tell. A SIDECAR codex run (inside a Claude host) has a different transcript
    # from the Claude session and is kept.
    own = (API.session_row(sid) or {}).get("transcript_path") or ""
    if own:
        out = {aid: r for aid, r in out.items()
               if (r.get("transcript") or "") != own}
    return sorted(out.values(), key=lambda r: r.get("started_at") or 0)

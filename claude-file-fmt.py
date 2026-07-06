#!/Users/z.yermagambet/.pyenv/versions/3.12.1/bin/python3
# claude-file-fmt.py — formatter for file-operation lines in the kitty mirror.
#
# Reads a Claude Code PostToolUse payload (JSON) on stdin for a Read/Edit/Write/
# MultiEdit/NotebookEdit tool and appends a compact one-liner to the mirror log
# (argv[1]) showing just the verb + file basename, e.g.
#
#   Read(README.md)
#   Update(claude-cmd-fmt.py)
#   Write(new_thing.py)
#
# Invoked directly as the PostToolUse hook. Verbs mirror Claude Code's own UI: Edit and
# MultiEdit show as "Update", Write as "Write", Read as "Read".
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import claude_hook as H
import claude_ops as O

A = O.A    # audit trail (real module, or a no-op stub if it failed to import)

# Verbs + colours are the shared claude_ops table (claude-substream.py renders a
# subagent's file ops with the SAME verbs/colours).
LABEL = O.FILE_LABEL


def fg(r, g, b):
    return f"\033[38;2;{r};{g};{b}m"


COLOR = {verb: fg(*rgb) for verb, rgb in O.FILE_RGB.items()}
DIM = fg(92, 99, 112)
DEF = fg(171, 178, 191)
RST = "\033[0m"


def main():
    d, LOG = H.read_payload()
    if d is None:
        return
    # A subagent's file ops are rendered by claude-substream.py (in transcript
    # order, with the subagent's messages) — skip them here to avoid duplication.
    if d.get("agent_id"):
        return H.ignore(d, "agent_id (substream owns rendering)")
    label = LABEL.get(d.get("tool_name") or "")
    if not label:
        return H.ignore(d, "not a file tool")
    ti = d.get("tool_input") or {}
    path = ti.get("file_path") or ti.get("notebook_path") or ""
    if not path:
        return H.ignore(d, "no file path")
    name = os.path.basename(path.rstrip("/")) or path
    failed = H.is_failure(d)
    if failed:
        col, mark = fg(*O.RED), DIM + " ✗" + RST          # red verb + ✗ on failure
    else:
        col, mark = COLOR.get(label, DEF), ""
    tool = d.get("tool_name") or ""
    tr = d.get("tool_response")
    added = removed = 0
    line = col + label + DIM + "(" + DEF + name + DIM + ")" + RST
    if not failed:
        if tool == "Read":
            # How much of the file it actually read ('' when the whole file). The result
            # carries startLine/numLines/totalLines; tool_input offset/limit is a fallback.
            finfo = tr.get("file") if isinstance(tr, dict) else None
            ext = O.read_extent(finfo, ti)
            if ext:
                line += "  " + DIM + ext + RST
        else:
            # Added/removed line counts for a mutation (Read returns (0, 0) → no suffix),
            # then the line range(s) it touched (from the result's structuredPatch).
            added, removed = O.diff_counts(tool, ti)
            parts = []
            if added:
                parts.append(fg(*O.GREEN) + f"+{added}" + RST)   # green additions
            if removed:
                parts.append(fg(*O.RED) + f"-{removed}" + RST)   # red removals
            if parts:
                line += "  " + " ".join(parts)
            rng = O.edit_range(tr.get("structuredPatch") if isinstance(tr, dict) else None)
            if rng:
                line += "  " + DIM + rng + RST
    line += mark
    O.emit(LOG, O.line(line))
    # Feed the session scoreboard (best-effort): the touched path (files counts
    # UNIQUE files — see bump()) plus the mutation's +/- line counts, keyed by the
    # raw tool name (Read/Edit/Write/MultiEdit/NotebookEdit) for the tools breakdown,
    # then the main session's own token spend since the last hook (see
    # claude_ops.bump_transcript).
    O.bump(LOG, tool=tool, file=path, added=added, removed=removed)
    O.bump_transcript(LOG, d.get("transcript_path"))
    A.hook_event(d, decision=f"rendered: {label}({name})"
                 + (" FAILED" if failed else
                    ("" if tool == "Read" else f" +{added} -{removed}")))


if __name__ == "__main__":
    H.run(main)

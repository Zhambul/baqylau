#!/Users/z.yermagambet/.pyenv/versions/3.12.1/bin/python3
# retarget-python.py — point every hook entry point at the *real* CPython binary
# instead of the pyenv shim, and undo it with --revert.
#
# WHY THIS EXISTS
# ---------------
# Every hook fires a fresh `python3`. When `python3` resolves to the pyenv shim
# (a bash script that re-runs `pyenv` on every call to pick a version), that shim
# costs ~140ms of pure overhead *per process* — measured 0.15s vs 0.01s for the
# concrete interpreter it eventually execs. A single PostToolUse fans out to five
# or more hook processes, so the shim tax dominates end-to-end hook latency by an
# order of magnitude, swamping the scripts' own ~5ms of imports.
#
# The two top-level entry shapes both hit the shim:
#   1. `/abs/path/claude-*.py …`  via the `#!/usr/bin/env python3` shebang, and
#   2. `python3 claude_audit.py hook subscriber`  in ~/.claude/settings.json.
# (Child processes are already fast: they spawn via sys.executable, which — once
# we're inside a shim-launched interpreter — is the concrete binary, not the shim.)
#
# This tool rewrites both shapes to an absolute concrete-interpreter path, chosen
# to respect pyenv's *active* selection (sys.executable under the shim already IS
# that selected binary). It is idempotent and re-runnable: run it again after a
# `pyenv` version change to re-point everything. `--revert` restores the portable
# `#!/usr/bin/env python3` shebang and the `python3` settings prefix.
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.expanduser("~/.claude/settings.json")
ENV_SHEBANG = "#!/usr/bin/env python3"

# A hook command's leading interpreter token: bare `python3`, or a concrete
# `.../bin/python3[.N]` we wrote on a previous run (so re-targeting is idempotent).
_CMD_PY = re.compile(r'("command":\s*")(python3|\S*/bin/python3(?:\.\d+)?)(\s)')


def real_interpreter():
    """The concrete interpreter to bake in — never the shim.

    Under the pyenv shim, sys.executable is already the selected version's real
    binary (the shim execs it), so it honours `pyenv version`. Only if we were
    somehow launched via the shim path itself do we fall back to resolving it.
    """
    exe = sys.executable or ""
    if exe and "/shims/" not in exe and os.access(exe, os.X_OK):
        return exe
    real = os.path.realpath(exe) if exe else ""
    if real and "/shims/" not in real and os.access(real, os.X_OK):
        return real
    raise SystemExit("could not resolve a concrete python3 (only found the shim)")


def _is_py_shebang(first_line):
    return first_line.startswith("#!") and "python" in first_line


def retarget_shebangs(interp, revert):
    new_line = ENV_SHEBANG if revert else "#!" + interp
    changed = []
    for name in sorted(os.listdir(HERE)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(HERE, name)
        with open(path, "r") as f:
            lines = f.readlines()
        if not lines or not _is_py_shebang(lines[0]):
            continue
        if lines[0].rstrip("\n") == new_line:
            continue
        lines[0] = new_line + "\n"
        with open(path, "w") as f:
            f.writelines(lines)
        changed.append(name)
    return changed


def retarget_settings(interp, revert):
    if not os.path.exists(SETTINGS):
        return None
    with open(SETTINGS, "r") as f:
        text = f.read()
    repl = "python3" if revert else interp
    new_text, n = _CMD_PY.subn(lambda m: m.group(1) + repl + m.group(3), text)
    if new_text != text:
        with open(SETTINGS, "w") as f:
            f.write(new_text)
        return n
    return 0    # matched, but already pointed at the target — nothing written


def main():
    revert = "--revert" in sys.argv[1:]
    interp = real_interpreter()
    sheb = retarget_shebangs(interp, revert)
    n_cmds = retarget_settings(interp, revert)
    target = ENV_SHEBANG if revert else interp
    print(f"{'reverted to' if revert else 'targeting'}: {target}")
    print(f"  shebangs rewritten : {len(sheb)}" + (f"  ({', '.join(sheb)})" if sheb else "  (already current)"))
    if n_cmds is None:
        print(f"  settings.json      : not found at {SETTINGS}")
    else:
        print(f"  settings commands  : {n_cmds} interpreter token(s) rewritten")
    if not revert:
        print("Re-run this after any `pyenv` version change to re-point the hooks.")


if __name__ == "__main__":
    main()

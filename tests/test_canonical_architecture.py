"""Mechanical dependency-direction checks for the canonical architecture."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def imports_under_path(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield path, alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield path, node.module


def imports_under(package: str):
    for path in (ROOT / package).rglob("*.py"):
        yield from imports_under_path(path)


def assert_imports(package: str, allowed_roots: set[str]):
    bad = []
    for path, imported in imports_under(package):
        root = imported.split(".", 1)[0]
        if root in {"app", "contracts", "core", "dashboard", "domain", "frontends", "plugins", "runtime", "terminal"}:
            if root not in allowed_roots:
                bad.append(f"{path.relative_to(ROOT)} imports {imported}")
    assert not bad, "invalid canonical imports:\n  " + "\n  ".join(bad)


def test_domain_imports_only_the_standard_library():
    assert_imports("domain", {"domain"})


def test_contracts_import_only_domain_and_the_standard_library():
    assert_imports("contracts", {"contracts", "domain"})


def test_runtime_imports_only_domain_and_contracts():
    assert_imports("runtime", {"domain", "contracts", "runtime"})


def test_presenters_do_not_import_plugins_or_each_other():
    assert_imports("terminal", {"domain", "contracts", "runtime", "terminal"})
    presentation_files = {
        "activity.py",
        "ansi.py",
        "highlight.py",
        "markdown.py",
        "presenter.py",
    }
    for path in (ROOT / "dashboard").glob("*.py"):
        if path.name not in presentation_files:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        assert not [name for name in imports if name.startswith(("plugins", "terminal", "core"))]


def test_shared_code_imports_no_concrete_plugin_descriptor():
    importers = []
    for path in ROOT.rglob("*.py"):
        if any(part in {".git", ".claude", "tests"} for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8")
        if "plugins.claude_code.plugin" in text or "plugins.codex.plugin" in text:
            importers.append(path.relative_to(ROOT).as_posix())
    assert importers == []


def test_harness_hook_and_pane_entries_live_only_in_their_plugin_folders():
    forbidden_entries = {
        "claude-hook.py",
        "claude-codex-hook.py",
        "claude-codex-session.py",
        "claude-split.py",
        "claude-mirror.py",
        "claude-scorebar.py",
        "claude-cmd-pre.py",
        "claude-copy.py",
        "claude-dashboard.py",
        "claude-audit.py",
        "claude-otlp-launch.py",
        "claude-otlp-receiver.py",
        "claude-statusline.py",
    }
    assert not forbidden_entries.intersection(path.name for path in (ROOT / "bin").iterdir())
    assert (ROOT / "plugins" / "claude_code" / "canonical_hook.py").is_file()
    assert (ROOT / "plugins" / "codex" / "canonical_hook.py").is_file()
    assert (ROOT / "app" / "terminal_panes.py").is_file()
    assert not (ROOT / "plugins" / "claude_code" / "split.py").exists()
    assert not [path.name for path in (ROOT / "bin").iterdir() if path.name.startswith("claude-")]


def test_claude_otel_is_not_a_top_level_plugin():
    assert not (ROOT / "plugins" / "otel").exists()
    assert (ROOT / "plugins" / "claude_code" / "otel" / "receiver.py").is_file()


def test_canonical_shared_code_imports_no_concrete_harness_package():
    canonical_shared_paths = [
        ROOT / "app",
        ROOT / "contracts",
        ROOT / "domain",
        ROOT / "runtime",
        ROOT / "terminal",
        ROOT / "dashboard" / "activity.py",
        ROOT / "dashboard" / "presenter.py",
    ]
    concrete_prefixes = ("plugins.claude_code", "plugins.codex")
    importers = []
    for shared_path in canonical_shared_paths:
        paths = shared_path.rglob("*.py") if shared_path.is_dir() else (shared_path,)
        for path in paths:
            for imported_path, imported in imports_under_path(path):
                if imported.startswith(concrete_prefixes):
                    importers.append(f"{imported_path.relative_to(ROOT)} imports {imported}")
    assert importers == []


def test_harness_plugins_do_not_import_each_other():
    forbidden_imports = {
        "claude_code": "plugins.codex",
        "codex": "plugins.claude_code",
    }
    importers = []
    for package_name, forbidden_prefix in forbidden_imports.items():
        for path in (ROOT / "plugins" / package_name).rglob("*.py"):
            for imported_path, imported in imports_under_path(path):
                if imported.startswith(forbidden_prefix):
                    importers.append(f"{imported_path.relative_to(ROOT)} imports {imported}")
    assert importers == []


def test_canonical_shared_code_contains_no_concrete_harness_vocabulary():
    shared_paths = [
        ROOT / "app",
        ROOT / "contracts",
        ROOT / "core",
        ROOT / "dashboard",
        ROOT / "domain",
        ROOT / "frontends",
        ROOT / "runtime",
        ROOT / "terminal",
    ]
    concrete_words = ("claude", "codex", "anthropic", "openai", "rollout", "transcript")
    violations = []
    for shared_path in shared_paths:
        paths = shared_path.rglob("*.py") if shared_path.is_dir() else (shared_path,)
        for path in paths:
            lowered = path.read_text(encoding="utf-8").lower()
            words = [word for word in concrete_words if word in lowered]
            if words:
                violations.append(f"{path.relative_to(ROOT)} contains {', '.join(words)}")
    assert violations == []


def test_no_read_path_orders_on_a_bare_occurred_at():
    """`occurred_at` is nullable BY DESIGN -- it is when the SOURCE said the fact happened.

    Sources that carry no timestamp of their own honestly leave it NULL, so every read
    path must fall back to `accepted_at` (when we recorded it). Ordering on the bare
    column sorts those events arbitrarily, which silently reorders a conversation.
    """
    violations = []
    for directory in ("app", "runtime", "dashboard", "terminal", "core", "plugins"):
        for path in sorted((ROOT / directory).rglob("*.py")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "occurred_at" not in line:
                    continue
                if "ORDER BY" not in line.upper():
                    continue
                if "COALESCE" in line.upper():
                    continue
                violations.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert violations == []


def test_dashboard_browser_code_has_no_concrete_harness_or_old_names():
    violations = []
    for path in sorted((ROOT / "dashboard" / "static").glob("app.*.js")):
        source = path.read_text(encoding="utf-8")
        source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
        source = re.sub(r"//.*$", "", source, flags=re.MULTILINE)
        for word in ("claude", "codex", "anthropic", "openai"):
            if re.search(rf"\b{word}\b", source, flags=re.IGNORECASE):
                violations.append(f"{path.relative_to(ROOT)} contains {word}")
        for abbreviation in ("sid", "ses", "op", "ops"):
            if re.search(rf"\b{abbreviation}\b", source):
                violations.append(
                    f"{path.relative_to(ROOT)} contains abbreviated {abbreviation}"
                )
    assert violations == []


def test_plugin_lifecycle_implementations_do_not_reenter_legacy_lifecycle_modules():
    forbidden = {
        ROOT / "plugins" / "codex" / "lifecycle.py": "plugins.codex.session",
    }
    violations = []
    for path, forbidden_import in forbidden.items():
        for _imported_path, imported in imports_under_path(path):
            if imported == forbidden_import:
                violations.append(f"{path.relative_to(ROOT)} imports {imported}")
    assert violations == []


def test_claude_foreground_hook_has_no_legacy_drawing_or_state_dependency():
    implementation_files = (
        ROOT / "plugins" / "claude_code" / "cmd_pre.py",
        ROOT / "plugins" / "claude_code" / "foreground.py",
        ROOT / "plugins" / "claude_code" / "shell.py",
    )
    forbidden_roots = {"app", "core", "dashboard", "frontends", "runtime", "terminal"}
    violations = []
    for path in implementation_files:
        for _imported_path, imported in imports_under_path(path):
            if imported.split(".", 1)[0] in forbidden_roots:
                violations.append(f"{path.relative_to(ROOT)} imports {imported}")
        source = path.read_text(encoding="utf-8")
        for forbidden_name in (
            "core.ops",
            "core.state",
            "hand_put",
            "fg-live",
            "spawn_streamer",
            "claude-stream.py",
        ):
            if forbidden_name in source:
                violations.append(f"{path.relative_to(ROOT)} contains {forbidden_name}")
    assert violations == []
    assert not (ROOT / "plugins" / "claude_code" / "foreground_process.py").exists()


def test_canonical_consumers_cannot_observe_or_checkpoint_native_sources():
    consumers = [
        ROOT / "app" / "terminal_process.py",
        ROOT / "app" / "scoreboard_process.py",
        ROOT / "dashboard" / "activity.py",
        ROOT / "dashboard" / "application.py",
        ROOT / "dashboard" / "presenter.py",
        ROOT / "dashboard" / "http" / "canonical.py",
        ROOT / "terminal" / "presenter.py",
        ROOT / "terminal" / "renderer.py",
        ROOT / "terminal" / "scoreboard.py",
    ]
    forbidden_fragments = (
        ".drain(",
        ".sources(",
        "CheckpointStore",
        "ObservationRunner",
        "SourceCheckpoint",
    )
    violations = []
    for path in consumers:
        source = path.read_text(encoding="utf-8")
        found = [fragment for fragment in forbidden_fragments if fragment in source]
        if found:
            violations.append(f"{path.relative_to(ROOT)} contains {', '.join(found)}")
    assert violations == []


def test_canonical_sse_has_no_broker_or_application_event_registry():
    source = (ROOT / "dashboard" / "http" / "canonical.py").read_text(encoding="utf-8")
    assert "DashboardEventStream" not in source
    assert "subscribe" not in source
    assert "queue.Queue" not in source
    assert not (ROOT / "dashboard" / "events.py").exists()
    assert not (ROOT / "dashboard" / "http" / "sse.py").exists()


def test_resume_and_sse_have_one_authoritative_path():
    launch_files = (
        ROOT / "contracts" / "harness.py",
        ROOT / "dashboard" / "http" / "canonical.py",
        ROOT / "dashboard" / "static" / "app.08-composer.js",
        ROOT / "dashboard" / "static" / "app.09-newsession.js",
        ROOT / "plugins" / "claude_code" / "launcher.py",
        ROOT / "plugins" / "codex" / "launcher.py",
    )
    assert not [
        path.relative_to(ROOT)
        for path in launch_files
        if "continue_latest" in path.read_text(encoding="utf-8")
    ]
    session_browser = (
        ROOT / "dashboard" / "static" / "app.05-session.js"
    ).read_text(encoding="utf-8")
    assert "stream.onerror" not in session_browser
    assert "SES_RECONNECT" not in session_browser


def test_plugin_packages_are_only_inert_package_markers():
    for package_path in (
        ROOT / "plugins" / "__init__.py",
        ROOT / "plugins" / "claude_code" / "__init__.py",
        ROOT / "plugins" / "codex" / "__init__.py",
    ):
        tree = ast.parse(package_path.read_text(encoding="utf-8"))
        executable_nodes = [
            node
            for node in tree.body
            if not (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            )
        ]
        assert executable_nodes == []


def test_legacy_dashboard_semantic_readers_and_handlers_are_deleted():
    assert not list((ROOT / "dashboard" / "read").glob("*.py"))
    assert not list((ROOT / "dashboard" / "control").glob("*.py"))
    assert sorted(path.name for path in (ROOT / "dashboard" / "http" / "post").glob("*.py")) == [
        "__init__.py",
        "files.py",
    ]
    assert not (ROOT / "plugins" / "host.py").exists()
    assert not (ROOT / "plugins" / "claude_code" / "hostctl.py").exists()
    assert not (ROOT / "plugins" / "codex" / "hostctl.py").exists()
    assert not list((ROOT / "dashboard" / "ext").rglob("*.py"))
    assert not list((ROOT / "dashboard" / "opshtml").glob("*.py"))


def test_descriptor_discovery_does_not_load_legacy_semantic_stores():
    program = """
import sys
from app.plugins import installed_plugins
installed_plugins()
forbidden = {'core.ops', 'core.state', 'core.sessionapi', 'plugins.host'}
loaded = sorted(forbidden.intersection(sys.modules))
if loaded:
    raise SystemExit(','.join(loaded))
"""
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


# --- Protocol implementations must say so ------------------------------------
#
# A Protocol is satisfied structurally, so a class can implement one by accident
# of method naming and, more importantly, can DRIFT out of one silently. Nothing
# in Python objects when a parameter is renamed or a method disappears; the break
# surfaces later, at a call site, in whichever harness happened to be running.
#
# So an implementation must name the Protocol it implements. The declaration is
# what makes the relationship greppable, what makes a type checker verify it, and
# what makes the divergence below a test failure instead of a bug report.
#
# It caught one immediately: CodexLifecycle.apply named its second parameter
# `recognized_session` where HarnessLifecycle.apply says `session`, so a keyword
# call would have worked on one harness and raised on the other.

SOURCE_PACKAGES = ("app", "contracts", "core", "dashboard", "domain",
                   "frontends", "plugins", "runtime", "terminal")

# Structural implementers that must NOT declare their Protocol, with the reason.
# Both are the same shape: the Protocol is declared in a layer that sits BELOW
# the implementer, so importing it to declare it would invert the dependency —
# the dashboard names the shape it needs from the application, and the
# application must not import the dashboard to say "yes, that is me". Moving
# these two Protocols into `contracts/` would retire both rows.
PROTOCOL_DECLARATION_EXEMPTIONS = {
    ("TerminalInputService", "TerminalSessionReader"):
        "the Protocol lives in dashboard/, which app/ may not import",
    ("ApplicationUsageState", "UsageReader"):
        "the Protocol lives in dashboard/, which app/ may not import",
}


def _method_signatures(node: ast.ClassDef) -> dict[str, tuple[str, ...]]:
    return {
        member.name: tuple(argument.arg for argument in member.args.args)
        for member in node.body
        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _protocols_and_classes():
    protocols: dict[str, dict[str, tuple[str, ...]]] = {}
    classes = []
    for package in SOURCE_PACKAGES:
        for path in (ROOT / package).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                bases = [ast.unparse(base) for base in node.bases]
                is_protocol = any(
                    base == "Protocol" or base.startswith("Protocol[") for base in bases
                )
                where = f"{path.relative_to(ROOT)}:{node.lineno}"
                if is_protocol:
                    protocols[node.name] = _method_signatures(node)
                else:
                    classes.append((where, node.name, bases, _method_signatures(node)))
    return protocols, classes


def _satisfies(members: dict, protocol: dict) -> bool:
    """Every protocol method is present with the same parameter NAMES.

    Names, not just arity: they are part of the contract because any of these
    may be called with keywords, and a renamed parameter is exactly the drift
    this test exists to catch. Matching on names is also what keeps the check
    precise -- `read(self, context)` and `read(self)` are different protocols,
    so a class does not accidentally implement one by owning a common verb.
    """
    return bool(protocol) and all(members.get(name) == args for name, args in protocol.items())


def test_protocol_implementations_declare_the_protocol_they_implement():
    protocols, classes = _protocols_and_classes()
    undeclared = []
    for where, name, bases, members in classes:
        matched = [p for p, signature in protocols.items() if _satisfies(members, signature)]
        if not matched or any(protocol in bases for protocol in matched):
            continue
        if all((name, protocol) in PROTOCOL_DECLARATION_EXEMPTIONS for protocol in matched):
            continue
        undeclared.append(f"{where} {name} implements {'/'.join(sorted(matched))} without saying so")
    assert undeclared == []


def test_a_declared_protocol_implementation_matches_the_protocol_exactly():
    """Declaring the Protocol must not become a way to inherit an empty method.

    Two failures hide behind an explicit base, and this is the test that catches
    the drift -- the one above only ever looks at classes that declare NOTHING.

    1. A Protocol's methods have `...` bodies, so subclassing one makes a MISSING
       implementation return None instead of raising AttributeError. The
       declaration would hide the very drift it exists to expose.
    2. A RENAMED parameter still satisfies the base class, silently. This is not
       hypothetical: CodexLifecycle.apply took `recognized_session` where
       HarnessLifecycle.apply says `session`, so the same keyword call worked on
       one harness and raised on the other.

    So a declaring class must define every member, with the same parameter names.
    """
    protocols, classes = _protocols_and_classes()
    divergent = []
    for where, name, bases, members in classes:
        for protocol in bases:
            signature = protocols.get(protocol)
            if signature is None:
                continue
            for member, arguments in sorted(signature.items()):
                if member not in members:
                    divergent.append(f"{where} {name} declares {protocol} but never defines {member}()")
                elif members[member] != arguments:
                    divergent.append(
                        f"{where} {name}.{member}{members[member]} does not match "
                        f"{protocol}.{member}{arguments}"
                    )
    assert divergent == []


def test_the_protocol_declaration_exemptions_are_all_still_real():
    """A stale exemption fails too -- the list may not outlive its reason."""
    protocols, classes = _protocols_and_classes()
    live = {
        (name, protocol)
        for _where, name, _bases, members in classes
        for protocol, signature in protocols.items()
        if _satisfies(members, signature)
    }
    assert sorted(set(PROTOCOL_DECLARATION_EXEMPTIONS) - live) == []

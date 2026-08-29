"""Mechanical dependency-direction checks for the canonical architecture."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from dataclasses import fields
from pathlib import Path

from domain.events import EVENT_TYPES

ROOT = Path(__file__).resolve().parents[1]

# Every package this repository owns. Discovery makes a new production package
# enter every tree-wide gate without an update to this test.
OWNED_SCRIPT_DIRECTORIES = {"bin", "client"}
OUR_PACKAGES = tuple(
    sorted(
        {
            path.parent.name
            for path in ROOT.glob("*/__init__.py")
            if path.parent.name != "tests"
        }
        | OWNED_SCRIPT_DIRECTORIES
    )
)


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


def assert_imports(package: str, allowed_roots: set[str], allowed_modules: frozenset[str] = frozenset()):
    """`allowed_modules` admits named MODULES from an otherwise closed package —
    the exception the harness boundary needs: it may name the terminal CONTRACT
    (which imports nothing of ours) without opening the whole package."""
    bad = []
    for path, imported in imports_under(package):
        root = imported.split(".", 1)[0]
        if root in OUR_PACKAGES:
            if root in allowed_roots:
                continue
            if any(imported == module or imported.startswith(module + ".") for module in allowed_modules):
                continue
            bad.append(f"{path.relative_to(ROOT)} imports {imported}")
    assert not bad, "invalid canonical imports:\n  " + "\n  ".join(bad)


# The one third-party name domain/ may say. It used to say none, and the price
# was a hand-written serializer: 120 lines walking `get_type_hints` to resolve
# unions, check Literals and decide that a Decimal is a string — a hand-written
# reimplementation of exactly this library, which the daemon already runs and
# which the api layer already depends on for the same job.
DOMAIN_DEPENDENCIES = {"pydantic"}


def test_domain_imports_only_the_standard_library_and_its_one_dependency():
    assert_imports("domain", {"domain"})
    outside = sorted(
        f"{path.relative_to(ROOT)} imports {imported}"
        for path, imported in imports_under("domain")
        if imported.split(".", 1)[0] not in OUR_PACKAGES
        and imported.split(".", 1)[0] not in sys.stdlib_module_names
        and imported.split(".", 1)[0] not in DOMAIN_DEPENDENCIES
    )
    assert outside == []
    # ...and it is really used, so the allowance cannot outlive the reason for it.
    assert any(imported.split(".", 1)[0] in DOMAIN_DEPENDENCIES for _path, imported in imports_under("domain"))


def _code_only(path: Path) -> str:
    """The file with its docstrings and comments removed.

    A rule about what code DOES must not fire on prose describing it — the
    first version of the database rule below failed on the module that explains
    why the driver may not be named outside one directory.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(ast.fix_missing_locations(tree))


def test_the_repository_layer_imports_only_the_model_layers():
    """The new floor: rows in, model objects out, and nothing above it named.

    It may stand on `domain` (the vocabulary), `audit.models` (the
    operational vocabulary, which imports only `domain.ids`), and `core` (where
    its three file paths live), and it may name the two model packages whose
    types its Protocols speak. Reaching for `engine`, `app`, `api`, `dashboard`
    or `notify` would mean the store could only run inside the daemon that
    composes it.
    """
    assert_imports(
        "repository",
        {"core", "audit", "domain", "repository"},
        # `harness.registry` is the one extra: a session hands out its plugin,
        # exactly as the store it replaced did, and the registry imports only
        # the contract.
        allowed_modules={"harness.models", "harness.registry", "terminal.models"},
    )


def test_only_a_repository_implementation_opens_a_database():
    """THE rule this whole layer exists to establish, and it has no exceptions.

    `sqlite3`, SQL text, and the driver's row type appear in exactly two
    directories, and both are repository implementations. The second one lives
    outside `repository/` only because it names a concrete harness, which the
    shared-vocabulary rule forbids in a shared package — it declares the same
    Protocol and speaks the same model objects as every other.

    Not "the daemon may": nothing may. A read-only forensic open is still a
    repository; an audit write from a hook process is still a repository.
    """
    allowed_prefixes = ("repository/impl/sqlite/",)
    allowed_files = {"harness/impl/codex/canonical/title.py"}
    markers = ("sqlite3", "SELECT ", "INSERT INTO", "UPDATE ", "DELETE FROM", "CREATE TABLE")
    violations = []
    for package in OUR_PACKAGES:
        for path in sorted((ROOT / package).rglob("*.py")):
            relative = path.relative_to(ROOT).as_posix()
            if relative.startswith(allowed_prefixes) or relative in allowed_files:
                continue
            # Read the CODE, not the prose: this file's own neighbours explain
            # the rule in their docstrings, and a comment naming the driver is
            # not a use of it.
            code = _code_only(path)
            found = [marker for marker in markers if marker in code]
            if found:
                violations.append(f"{relative} contains {', '.join(found)}")
    assert violations == []


def test_repository_contracts_expose_no_connection_or_transaction():
    """A repository method is ONE whole transaction, decided inside it.

    An earlier draft of this layer handed callers a unit of work — a context
    manager over the repositories — so the interpreter could span three tables.
    That put transaction management back in the caller, which is the thing the
    layer exists to remove. The multi-table write is one coarse method now, and
    this is what stops the handle growing back.
    """
    forbidden_names = {"connect", "connection", "cursor", "transaction", "unit_of_work", "begin", "commit", "rollback"}
    forbidden_returns = ("Connection", "Cursor", "AbstractContextManager", "Iterator", "Generator")
    violations = []
    for path in sorted((ROOT / "repository" / "contract").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            where = f"{path.relative_to(ROOT)}:{node.lineno}"
            if node.name in forbidden_names:
                violations.append(f"{where} exposes {node.name}()")
            returns = ast.unparse(node.returns) if node.returns else ""
            for forbidden in forbidden_returns:
                if forbidden in returns:
                    violations.append(f"{where} returns {returns}")
    assert violations == []


def test_exactly_two_database_files_are_named():
    """Seven files became two, and the count is the point.

    `main.db` is everything the application owns and reads back; `audit.db` is
    separate because every short-lived process writes it and because it is what
    you read when `main.db` is the suspect. There is no third: the daemon's pid
    claim lived in `locks.db` until the port it binds became the only answer.
    Nothing else may appear.
    """
    named = set()
    for package in OUR_PACKAGES:
        for path in sorted((ROOT / package).rglob("*.py")):
            if path.relative_to(ROOT).as_posix() == "harness/impl/codex/canonical/title.py":
                continue  # the foreign index; its name is Codex's, not ours
            named.update(re.findall(r'"([A-Za-z0-9_.-]+\.(?:db|sqlite))"', path.read_text(encoding="utf-8")))
    assert named == {"main.db", "audit.db"}


def test_no_key_value_table_exists():
    """Entities have identities. Nine JSON blobs under nine keys became nine tables.

    Six opaque columns survive and each is deliberate: the canonical payload is
    a closed vocabulary the codec validates on both encode and decode, the raw
    payload is the verbatim bytes we observed, an audit's content is free-form by
    contract — recorded, never queried — and the three read-model payloads are
    closed typed documents of `domain/sessiondata.py` and `domain/entries.py`,
    validated the same way the canonical one is.
    """
    schema = (ROOT / "repository" / "impl" / "sqlite" / "schema.py").read_text(encoding="utf-8")
    allowed_opaque = {
        "canonical_events.payload",
        "raw_events.payload",
        "state_files.content",
        # The read model: closed typed documents, validated on both sides by the
        # codec, versioned by the schema version — not a key-value store.
        "session_data.payload",
        "session_data_actors.payload",
        "session_entries.payload",
    }
    tables = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)\((.*?)\n\);", schema, re.S)
    violations = []
    for table, body in tables:
        columns = [
            line.strip().split()[0]
            for line in body.splitlines()
            if line.strip() and not line.strip().startswith(("PRIMARY", "FOREIGN", "UNIQUE", "--"))
        ]
        for column in columns:
            if column in ("val", "value", "json", "data", "blob"):
                violations.append(f"{table}.{column} is a key-value column")
            if column == "payload" and f"{table}.payload" not in allowed_opaque:
                violations.append(f"{table}.payload is an undeclared opaque column")
            if column == "content" and f"{table}.content" not in allowed_opaque:
                violations.append(f"{table}.content is an undeclared opaque column")
        if set(columns) in ({"key", "val"}, {"key", "value"}, {"name", "value"}):
            violations.append(f"{table} is a key-value table")
    assert violations == []
    assert len(tables) > 25  # a schema that parsed to nothing would pass vacuously


def test_the_harness_contract_and_models_import_only_domain_and_the_terminal_contract():
    """The floor of the harness layer, the twin of the terminal one below it.

    The terminal contract is the one thing it may reach sideways for: a
    harness's control context is handed a terminal, and the alternative is an
    untyped field. It is safe because the terminal contract and its models
    import NOTHING of ours (pinned below), so no cycle can form.
    """
    boundary = [ROOT / "harness" / "contract.py", *sorted((ROOT / "harness" / "models").rglob("*.py"))]
    allowed_modules = {"terminal.contract", "terminal.models"}
    bad = []
    for path in boundary:
        for _path, imported in imports_under_path(path):
            root = imported.split(".", 1)[0]
            if root in {"harness", "domain"}:
                continue
            if any(imported == module or imported.startswith(module + ".") for module in allowed_modules):
                continue
            if root in {"api", "app", "core", "dashboard", "audit", "engine", "notify", "terminal"}:
                bad.append(f"{path.relative_to(ROOT)} imports {imported}")
    assert bad == []


def test_the_harness_implementations_never_import_the_application():
    """`harness/impl/` sits below the graph that composes it.

    A harness may use the contract, the domain, and core utilities; reaching
    for `app/`, `api/` or `dashboard/` would mean a plugin could only run
    inside the daemon — and the hook entries, which run in the harness's own
    process tree, could not import their own package.
    """
    assert_imports(
        "harness",
        {"core", "audit", "domain", "harness", "repository"},
        allowed_modules={
            # the terminal a control context is handed, and the two session-level
            # services the application tier drives it through
            "terminal.contract",
            "terminal.models",
            "terminal.adapter",
            "terminal.launch",
            # the hook client runs OUTSIDE the daemon and observes its own window
            "terminal.impl",
        },
    )


def test_the_terminal_contract_and_models_import_nothing_of_ours():
    """The floor of the terminal layer: window ids in, typed responses out.

    Keeping it free of `domain`, `harness`, and the rest is what lets the
    harness contract name it, what keeps sessions out of the terminal
    abstraction, and what makes a second terminal implementable against one
    small file.
    """
    boundary = [ROOT / "terminal" / "contract.py", *sorted((ROOT / "terminal" / "models").rglob("*.py"))]
    foreign = []
    for path in boundary:
        for _path, imported in imports_under_path(path):
            root = imported.split(".", 1)[0]
            if root not in {"terminal", "dataclasses", "enum", "typing", "__future__"}:
                foreign.append(f"{path.relative_to(ROOT)} imports {imported}")
    assert foreign == []


def test_only_the_provider_graph_resolves_a_terminal():
    """`terminal/impl/` has one door and ONE caller.

    Everything else takes a `TerminalPlugin` (or one of its five fields) by
    injection. It used to have three: a hook process and the pane keybinding
    resolved a terminal directly, because they run INSIDE a window and are the
    only things that can observe which one. They are stdlib-only clients now
    (`client/`) and read the variable that names the window straight out of their
    own environment, so the door has no callers left outside the daemon.
    """
    allowed = {"app/providers.py"}
    importers = set()
    for package in OUR_PACKAGES:
        for path, imported in imports_under(package):
            relative = path.relative_to(ROOT).as_posix()
            if relative.startswith("terminal/impl/"):
                continue  # the implementations are the thing being resolved
            if imported == "terminal.impl" or imported.startswith("terminal.impl."):
                importers.add(relative)
    assert importers == allowed


def test_no_terminal_is_named_outside_its_own_implementation():
    """The terminal twin of the harness-vocabulary gate.

    A concrete terminal's name may appear in exactly one directory — its own.
    The structural leaks this cannot see (a match expression built above the
    boundary) are what `PaneAnchor` and the contract exist to prevent; this
    stops the vocabulary growing back.
    """
    concrete_words = ("kitty", "kitten")
    scanned = [
        ROOT / "api",
        ROOT / "app",
        ROOT / "core",
        ROOT / "dashboard",
        ROOT / "audit",
        ROOT / "domain",
        ROOT / "harness",
        ROOT / "engine",
        ROOT / "notify",
        ROOT / "terminal",
    ]
    implementation = ROOT / "terminal" / "impl" / "kitty"
    # The detector registry is the one file above the implementation that may
    # name it: routing a name to a directory is exactly what it does.
    registry = ROOT / "terminal" / "impl" / "__init__.py"
    violations = []
    for directory in scanned:
        for path in sorted(directory.rglob("*.py")):
            if implementation in path.parents or path == registry:
                continue
            lowered = path.read_text(encoding="utf-8").lower()
            words = [word for word in concrete_words if word in lowered]
            if words:
                violations.append(f"{path.relative_to(ROOT)} contains {', '.join(words)}")
    for path in sorted((ROOT / "dashboard" / "static").glob("*.js")):
        if "kitty" in path.read_text(encoding="utf-8").lower():
            violations.append(f"dashboard/static/{path.name} contains kitty")
    assert violations == []


def test_the_engine_imports_only_the_domain_and_the_harness_contract():
    """The engine is the neutral middle: evidence in, facts out.

    It may stand on the floor (`core/`, `audit/`) and name the harness
    CONTRACT, because it drives plugins it is handed. Reaching UP — for `app/`,
    `api/`, `dashboard/`, `terminal/` or a concrete harness — would mean the
    store could only run inside the daemon that composes it.
    """
    assert_imports(
        "engine",
        {"core", "audit", "domain", "engine", "repository"},
        allowed_modules={"harness.contract", "harness.models", "harness.registry"},
    )


def test_the_sdk_is_an_outside_client_of_the_http_api():
    """The SDK can know the wire contract, but it cannot know the application graph."""
    assert_imports("sdk", {"api", "sdk"})


def test_the_audit_write_tier_is_a_floor_and_the_read_tier_is_the_daemons():
    """Everything writes audit; only the daemon reads them back.

    `audit/record.py` is reached from hook processes, pane renderers and
    the daemon alike — a floor, like `core/`. `audit/read.py` opens the
    database read-only to answer the dashboard, so a writer that imports it has
    either grown a reporting job or paid for a tier it never uses.
    """
    readers = set()
    for package in OUR_PACKAGES:
        for path, imported in imports_under(package):
            if path.relative_to(ROOT).as_posix().startswith("repository/"):
                continue  # the layer that DEFINES both tiers
            if "AuditReadRepository" in path.read_text(encoding="utf-8"):
                readers.add(path.relative_to(ROOT).as_posix())
            del imported
    assert readers == {
        "app/providers.py",
        "app/services/insights.py",
        "dashboard/services/workspace.py",
    }


def test_the_terminal_tier_imports_no_concrete_harness():
    # No `harness` package root: the dependency runs the other way (the harness
    # contract names the terminal one), and a package-level cycle would be the
    # first step back to two interfaces. The panes tier may name the harness
    # CONTRACT — a pane reacts to canonical facts — and nothing concrete.
    assert_imports(
        "terminal",
        {"core", "audit", "domain", "engine", "repository", "terminal"},
        allowed_modules={"harness.contract", "harness.models"},
    )


# test_the_render_tier_is_inert lived here. `dashboard/render/` is gone: the
# Svelte frontend builds markup from the entries the read model serves, so there
# is no daemon-side render tier left to keep inert. Vitest and Playwright own the
# replacement behavior gates.


def test_shared_code_imports_no_concrete_plugin_descriptor():
    importers = []
    for path in ROOT.rglob("*.py"):
        if any(part in {".git", ".claude", ".venv", "tests"} for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8")
        if "harness.impl.claude_code.plugin" in text or "harness.impl.codex.plugin" in text:
            importers.append(path.relative_to(ROOT).as_posix())
    assert importers == []


def test_no_process_outside_the_daemon_lives_inside_the_application():
    """Every program the daemon does not own is a file in `client/` (R1).

    They used to live in five packages and sixteen files — a published wrapper
    beside its implementation beside the daemon-side code it POSTs to — which is
    why "is this file a client?" had no mechanical answer and no rule about one
    could be enforced. The rules themselves are in
    tests/test_canonical_clients.py; this is the absence half.

    The `bin/` directories those published paths lived in are gone too: external
    configuration names `client/` directly now. During the migration they were
    symlinks into it, which is how sessions that had already captured the old
    paths kept delivering.
    """
    gone = (
        "harness/impl/claude_code/hooks/entry.py",
        "harness/impl/claude_code/hooks/statusline.py",
        "harness/impl/claude_code/otel/receiver.py",
        "harness/impl/codex/hooks/entry.py",
        "harness/hooks/client.py",
        "core/daemon/client.py",
        "terminal/panes/client.py",
        "terminal/panes/mirror_process.py",
        "terminal/panes/scoreboard_process.py",
    )
    assert [name for name in gone if (ROOT / name).exists()] == []
    for directory in ("harness/impl/claude_code/bin", "harness/impl/codex/bin", "terminal/bin"):
        assert not (ROOT / directory).exists(), f"{directory} is back"


def test_harness_hook_and_pane_entries_do_not_come_back_to_bin():
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
    assert not (ROOT / "harness" / "impl" / "claude_code" / "split.py").exists()
    assert not [path.name for path in (ROOT / "bin").iterdir() if path.name.startswith("claude-")]


# The anchor-depth rule that used to live here is gone, and so is the
# wrapper rule beside it. Both were the best checks available on a design where
# sixteen files each counted the directories between themselves and the root, and
# neither could stop what that design actually did (see the pane outage in
# tests/test_canonical_clients.py). They are replaced by
# test_nothing_but_two_dev_entries_walks_up_to_the_repository_root and
# test_the_published_client_paths_are_an_api, which check that no file names
# anything but its own directory and that the paths external configuration holds
# still exist and still RUN.


# The routes that answer with BYTES rather than a model, and therefore name a
# raw Response: static assets, the OpenAPI document, the evidence plane's write
# endpoint (whose reply is the harness's own bytes), the file-content reader,
# and the four streams (whose FRAMES are models — see api/sse.py — but whose
# response is an open connection).
# Handlers that answer something other than one of this layer's models: files,
# a redirect, a raw YAML document, an SSE stream. Named by HANDLER, not by path,
# so a route that moves package keeps its exemption and a route that changes
# shape loses it. (`content` and `pane_stream` were here; the content route and
# the daemon-side pane streams are both gone.)
RAW_RESPONSE_ROUTES = {
    "index",
    "build_asset",
    "static",
    "service_worker",
    "favicon",
    "openapi_yaml",
    "record_hook_delivery",
    "global_stream",
    "session_stream",
}


def _route_handlers():
    """Every route handler in api/, with its decorators, as (path, node) pairs."""
    for path in sorted((ROOT / "api").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            decorators = [ast.unparse(item) for item in node.decorator_list]
            if any(item.startswith(("router.", "guarded.", "web.")) for item in decorators):
                yield path, node, decorators


def _binding_modules(path: Path) -> dict[str, str]:
    """Which module each name in this file was imported FROM."""
    bindings = {}
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                bindings[alias.asname or alias.name] = node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bindings[alias.asname or alias.name.split(".")[0]] = alias.name
    return bindings


def test_every_route_answers_with_a_model_the_api_layer_owns():
    """The browser contract is written down HERE, not inferred from a read model.

    Two failures this replaces. 26 handlers used to answer with a bare
    `JSONResponse` built by reflecting over whatever a service returned, so
    renaming an internal field silently changed the JSON and `/openapi.yaml`
    described none of them. Then the ones that did declare a model declared a
    DASHBOARD or HARNESS dataclass — `response_model=list[DashboardSessionListItem]`,
    `response_model=ControlOutcome` — which made a projection's field list the
    published wire contract by accident: the api layer had no say, and a fold
    that renamed a field renamed it for every browser.

    So: a route names a return type, and every type it names is defined under
    api/. The mapping from the service object to the model is the api layer's
    own code (`SessionListItemResponse.of(...)`), which is where the decision
    to expose a field belongs.
    """
    outside = []
    for path, node, decorators in _route_handlers():
        if node.name in RAW_RESPONSE_ROUTES:
            continue
        if not node.returns:
            outside.append(f"{path.relative_to(ROOT)}:{node.name} has no return type")
            continue
        bindings = _binding_modules(path)
        declared = [ast.unparse(node.returns)]
        declared += [
            item.split("response_model=", 1)[1].split(",")[0] for item in decorators if "response_model=" in item
        ]
        for name in {token for text in declared for token in re.findall(r"[A-Za-z_][A-Za-z_0-9]*", text)}:
            module = bindings.get(name)
            if module is None or module.startswith("api."):
                continue
            outside.append(
                f"{path.relative_to(ROOT)}:{node.name} answers with {name}, which is {module}'s and not the api layer's"
            )
    assert outside == []


def test_no_response_anywhere_is_a_hand_built_document():
    """One encoder, and it is the models'.

    `JSONResponse` takes any object at all and reflects it onto the wire, which
    is how a route came to answer with a shape its own `response_model` did not
    describe — FastAPI validates nothing it did not serialize itself. It is
    banned outright (ruff's TID251 says so too, so the failure lands at lint
    time), and so is the `json` module inside api/: an error body, an SSE frame
    and a route's reply are all a model, serialized by pydantic.

    The dashboard's `json_ready` — a second encoder that walked dataclass trees
    into dicts — is gone with them.
    """
    offenders = []
    for path in sorted((ROOT / "api").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(a.name == "json" for a in node.names):
                offenders.append(f"{path.relative_to(ROOT)} imports json")
            if isinstance(node, ast.ImportFrom) and node.module in ("json", "fastapi.responses", "starlette.responses"):
                for alias in node.names:
                    if node.module == "json" or alias.name == "JSONResponse":
                        offenders.append(f"{path.relative_to(ROOT)} imports {alias.name}")
    assert offenders == []
    assert not (ROOT / "dashboard" / "render" / "serialize.py").exists()
    assert not [
        path
        for package in OUR_PACKAGES
        for path in (ROOT / package).rglob("*.py")
        if "JSONResponse" in path.read_text(encoding="utf-8")
    ]


# The application runtime is the composition root. The CLI imports it inline so
# that importing the CLI does not pull FastAPI in. The SDK is an outside client
# of the wire contract. Every other name below api/ would invert the direction.
API_CONSUMERS = {Path("dashboard/cli.py")}
API_CONSUMER_PACKAGES = {"sdk"}


def test_nothing_below_the_api_layer_knows_it_exists():
    """The direction is one-way: api/ maps the services' objects onto the wire,
    and no service, projection, harness or renderer has ever heard of a request.

    Enforced because the api DTO layer only means anything while it holds: the
    moment a service imports a response model, the model stops being the api
    layer's own statement about the wire and becomes shared vocabulary again —
    which is exactly the coupling the DTOs were introduced to break.
    """
    reaching = [
        f"{path.relative_to(ROOT)} imports {imported}"
        for package in OUR_PACKAGES
        if package != "api"
        for path, imported in imports_under(package)
        if (imported == "api" or imported.startswith("api."))
        and package not in API_CONSUMER_PACKAGES
        and path.relative_to(ROOT) not in API_CONSUMERS
    ]
    assert reaching == []
    # ...and the one allowed consumer really is one, so the exemption cannot
    # quietly cover a file that stopped importing it.
    assert any(
        imported == "api" or imported.startswith("api.")
        for _path, imported in imports_under_path(ROOT / "dashboard" / "cli.py")
    )


def test_the_json_allowlist_is_only_foreign_documents():
    """`json` is banned tree-wide, and every exemption is still needed.

    A document of OURS is a dataclass or a pydantic model; something else turns
    it into bytes. Building one as a dict literal is what let the canonical
    envelope's twelve field names live in four places at once, and what let a
    route answer with a shape its own `response_model` did not describe.

    ruff's TID251 does the banning (ruff.toml), which puts the failure on the
    line that did it. This holds the exemption list to files that STILL need
    one: an entry for a file that no longer touches json is an entry that would
    silently cover the next hand-built document written there.
    """
    configuration = (ROOT / "ruff.toml").read_text(encoding="utf-8")
    exempt = [
        line.split('"')[1]
        for line in configuration.splitlines()
        if line.startswith('"') and "TID251" in line and "=" in line
    ]
    assert exempt, "the allowlist parsed as empty; this test would pass vacuously"

    stale = []
    for pattern in exempt:
        if pattern.startswith("tests/"):
            continue
        matched = sorted(ROOT.glob(pattern))
        assert matched, f"{pattern} matches no file"
        if not any("json." in path.read_text(encoding="utf-8") for path in matched):
            stale.append(pattern)
    assert stale == [], f"exempt from the json ban and no longer using it: {stale}"

    # ...and nothing OUTSIDE the list uses it, which is the ban itself. Checked
    # here as well as by ruff so that a run of the suite alone still catches it.
    covered = {path for pattern in exempt for path in ROOT.glob(pattern)}
    offenders = sorted(
        str(path.relative_to(ROOT))
        for package in OUR_PACKAGES
        for path in (ROOT / package).rglob("*.py")
        if path not in covered and _calls_json(path)
    )
    assert offenders == []


def _calls_json(path: Path) -> bool:
    """A real call, not the word in a comment — read off the syntax tree, so
    that a file EXPLAINING why it no longer encodes by hand does not read as a
    file that does."""
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if (
            isinstance(function, ast.Attribute)
            and function.attr in ("dumps", "loads")
            and isinstance(function.value, ast.Name)
            and function.value.id == "json"
        ):
            return True
    return False


def _owned_python_files():
    return sorted(
        path
        for package in OUR_PACKAGES
        for path in (ROOT / package).rglob("*.py")
        if not any(part in {"__pycache__", "node_modules"} for part in path.parts)
    )


def test_harness_adapters_never_use_the_raw_json_codec():
    """Foreign JSON crosses an adapter boundary through a typed Pydantic model.

    Importing the stdlib codec is forbidden here, rather than allowlisted: a
    direct ``json.load(s)`` creates an untyped intermediate before validation,
    while ``model_validate_json`` validates bytes/text as it decodes them and
    ``model_dump_json`` serializes a declared model directly.
    """
    violations = []
    for path in sorted((ROOT / "harness" / "impl").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names if alias.name == "json"]
                if names:
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno} imports json")
            elif isinstance(node, ast.ImportFrom) and node.module == "json":
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno} imports from json")
    assert violations == [], "raw JSON codec use in harness adapters:\n  " + "\n  ".join(violations)


def test_owned_packages_never_use_raw_dictionaries_or_jsonvalue():
    """Documents and intermediate records must have declared shapes.

    Dictionaries are allowed only for the exact typed registry/index symbols
    below, where keyed dynamic lookup is the data structure's actual behavior.
    The exemption is symbol-level, never file-level: payloads and intermediate
    records in the same modules still have to be dataclasses or Pydantic models.
    """
    typed_registry_allowlist = {
        "api/app.py": {"FrameworkDocument"},
        "api/config.py": {"SECURITY_HEADERS"},
        "api/controls/routes.py": {
            "CONTROL_RESPONSES",
            "CONTROL_STATUS",
            "LAUNCH_RESPONSES",
            "LAUNCH_STATUS",
        },
        "api/middleware.py": {"headers"},
        "api/runtime.py": {"base_environment", "environment"},
        "api/sessiondata/routes.py": {"known"},
        "api/sse.py": {"NO_STORE"},
        "api/terminal/panes.py": {"PANE_RESPONSES"},
        "api/application/files.py": {"DICTATION_RESPONSES", "UPLOAD_RESPONSES"},
        "api/application/static.py": {"_BUILD_TYPES", "headers", "router"},
        "api/hooks/routes.py": {"HOOK_RESPONSES"},
        "api/telemetry/models/browser_events_request.py": {"connection", "details"},
        "api/application/models/preferences/global_application_response.py": {
            "hidden_directories"
        },
        "api/application/models/preferences/hidden_directories_response.py": {"hidden"},
        "api/responses.py": {
            "Documented",
            "EVERY_ROUTE",
            "documented",
            "statuses",
        },
        "app/injection.py": {"Instances", "dependencies", "instances"},
        "app/providers.py": {"display_by_harness", "translators"},
        "app/services/insights.py": {
            "daily_counts",
            "grouped",
            "hourly_counts",
            "project_counts",
        },
        "audit/failures.py": {"_states"},
        "audit/record.py": {"_recorders"},
        "client/_http.py": {"PANE_COMMAND_PATHS"},
        "client/_handoff.py": {"published_targets"},
        "client/_daemon.py": {"request_headers"},
        "client/claude_hook.py": {"reply"},
        "client/codex_hook.py": {"reply"},
        "client/claude_otel.py": {"TELEMETRY_HEADERS"},
        "client/_model.py": {"ATTENTION_TWINS", "_entries", "_shells", "actors"},
        "client/_render.py": {
            "FILE_VERBS",
            "PLAN_DECISIONS",
            "TASK_MARKERS",
            "targets",
            "tokens",
            "tools",
            "totals",
        },
        "client/terminal_pane.py": {"EMPTY_TARGETS", "_published"},
        "dashboard/cli.py": {"LAUNCH_VARIABLES", "variables"},
        "dashboard/config.py": {"STATIC"},
        "dashboard/dictate.py": {"request_headers"},
        "dashboard/services/preferences.py": {"hidden_directories"},
            "dashboard/services/workspace.py": {"_terminal_text", "pending_questions"},
        "domain/entries.py": {"BODY_TYPES", "ENTRY_TYPES", "open_attentions"},
        "domain/events.py": {"EVENT_TYPES", "PAYLOAD_TYPES"},
        "engine/interpret/loop.py": {"identities"},
        "engine/interpret/liveness.py": {"_terminal_owners"},
        "engine/react/loop.py": {"EMPTY_BODY_SUSPECT", "actors", "known", "states"},
        "engine/sessiondata/actors.py": {
            "FILE_TOOLS",
            "counts",
            "finished_actors",
            "idle_actors",
        },
        "engine/sessiondata/contract.py": {"actors", "merged"},
        "engine/sessiondata/session.py": {"known"},
        "engine/sessiondata/naming.py": {"EMPTY_DISPLAY_BY_HARNESS"},
        "harness/models/interrupts.py": {"_marked_at"},
            "harness/models/selections.py": {"_efforts", "_models"},
            "harness/registry.py": {"_plugins"},
                "harness/services/terminal_gate.py": {"_locks"},
            "harness/services/control_effects.py": {"assignments", "shells", "turns"},
        "harness/impl/claude_code/catalog.py": {"COMMAND_PROMPT_FLOORS"},
        "harness/impl/claude_code/canonical/messages.py": {
            "BACKGROUND_OUTCOMES",
            "notifications_by_actor",
        },
        "harness/impl/claude_code/canonical/transcript.py": {"parents"},
        "harness/impl/claude_code/canonical/translator.py": {
            "_pending_compactions",
        },
        "harness/impl/claude_code/canonical/turns.py": {"_response_turns"},
        "harness/impl/claude_code/canonical/sources.py": {"notifications_by_actor"},
            "harness/impl/claude_code/canonical/toolcalls.py": {
                "CHROME_ACTIONS",
                "TOOL_KINDS",
            "FILE_ACTIONS",
            "calls",
            "agent_assignments",
            "monitors",
            "background_tasks",
        },
        "harness/impl/claude_code/usage/live.py": {"samples"},
        "harness/impl/claude_code/controls/controller.py": {"HANDLERS"},
        "harness/impl/claude_code/controls/rewindmenu.py": {"MODE_LABELS"},
        "harness/impl/claude_code/model.py": {"ALIAS_DISPLAY"},
        "harness/impl/codex/canonical/events.py": {"EVENTS"},
        "harness/impl/codex/canonical/items.py": {"RESPONSES"},
        "harness/impl/codex/canonical/records.py": {
            "ITEM_COMPLETED_ITEMS",
            "COLLABORATION_ARGUMENTS",
        },
        "harness/impl/codex/canonical/rollout.py": {"_TOP"},
        "harness/impl/codex/canonical/sources.py": {
            "_directories",
            "_rollouts",
            "_child_parent_by_path",
            "_sessions",
            "_child_sources",
            "existing",
            "grouped",
        },
        "harness/impl/codex/canonical/translator.py": {
            "CODEX_TOOLS",
            "GOAL_STATES",
            "ACTIVITY_CALLS",
            "FILE_ACTIONS",
            "_collaboration_calls",
            "_process_shells",
            "_continuation_shells",
            "_call_records",
            "_plan_tasks",
            "current",
            "_goals",
            "_working_directories",
            "_active_turns",
            "_compactions",
            "_mcp_tool_outcomes",
            "_sources_by_session",
            "result_calls",
        },
        "harness/impl/codex/controls/controller.py": {"HANDLERS", "handlers"},
        "harness/runtime.py": {"_by_harness", "by_harness"},
        "harness/impl/codex/continuity.py": {
            "_pending_by_window",
            "_resolved_by_session",
        },
        "harness/impl/codex/controls/modeldialog.py": {"EFFORT_LABEL"},
        "harness/impl/codex/usage_rows.py": {"WINDOW_LABELS"},
        "inference/default.py": {"EXECUTABLE_VARIABLES", "retries"},
        "notify/channels/webpush.py": {"request_headers"},
        "notify/notifier.py": {
            "NOTIFICATION_KINDS",
            "current_states",
            "delivered",
            "items_by_session",
            "pending",
            "previous_states",
        },
        "notify/presence.py": {"subs", "viewing"},
        "repository/impl/sqlite/audit.py": {"EMPTY_ERROR_COUNTS", "counts"},
        "repository/impl/sqlite/connection.py": {"EMPTY_MIGRATIONS"},
        "repository/impl/sqlite/raw_event_audits.py": {"by_raw_event"},
        "repository/impl/sqlite/raw_events.py": {"EMPTY_POSITIONS", "positions"},
        "repository/impl/sqlite/schema.py": {"MAIN_MIGRATIONS"},
        "repository/impl/sqlite/session_data.py": {
            "actors_by_session",
            "leads",
            "newest",
        },
        "repository/mapper/workspace.py": {"by_prompt"},
        "sdk/client.py": {"headers", "parameters", "query"},
        "sdk/state.py": {"folded", "open_by_actor"},
        "sdk/transport.py": {"JSON_HEADERS"},
        "terminal/adapter.py": {
            "activity_tags",
            "cleared_tags",
            "on_screen",
            "outcomes",
            "scoreboard_tags",
        },
        "terminal/impl/__init__.py": {"DETECTORS"},
        "terminal/impl/kitty/plugin.py": {"EMPTY_TAGS", "SPLIT_LOCATIONS", "colors"},
        "terminal/impl/kitty/remote.py": {"colors", "user_vars"},
        "terminal/impl/pty/keys.py": {"NAMED_KEYS"},
        "terminal/impl/pty/plugin.py": {
            "child_environment",
            "environment",
            "launch_environment",
            "windows",
        },
        "terminal/impl/pty/window.py": {
            "descendant_identities",
            "identities",
            "observed",
            "tags",
        },
        "terminal/tabs.py": {"_painted"},
        "terminal/theme.py": {"TAB_APPEARANCES"},
    }

    def assigned_name(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str | None:
        current = node
        while current in parents:
            current = parents[current]
            if isinstance(current, ast.arg):
                return current.arg
            if isinstance(current, ast.AnnAssign):
                if isinstance(current.target, ast.Name):
                    return current.target.id
                if isinstance(current.target, ast.Attribute):
                    return current.target.attr
            if isinstance(current, ast.Assign) and len(current.targets) == 1:
                target = current.targets[0]
                if isinstance(target, ast.Name):
                    return target.id
                if isinstance(target, ast.Attribute):
                    return target.attr
                return None
            if isinstance(current, (ast.FunctionDef, ast.ClassDef, ast.Module)):
                return None
        return None

    violations = []
    for path in _owned_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents: dict[ast.AST, ast.AST] = {
            child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(tree):
            where = f"{path.relative_to(ROOT)}:{getattr(node, 'lineno', 1)}"
            relative_path = str(path.relative_to(ROOT))
            allowed_registries = typed_registry_allowlist.get(relative_path, set())
            if isinstance(node, (ast.Dict, ast.DictComp)) and assigned_name(node, parents) not in allowed_registries:
                violations.append(f"{where} contains a dictionary literal")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "model_dump":
                violations.append(f"{where} materializes model_dump() as a dictionary")
            elif (
                isinstance(node, ast.Name)
                and node.id in {"dict", "Dict"}
                and assigned_name(node, parents) not in allowed_registries
            ):
                violations.append(f"{where} uses the raw dictionary type")
            elif isinstance(node, ast.Attribute) and node.attr in {"dict", "Dict"}:
                violations.append(f"{where} uses the raw dictionary type")
            elif isinstance(node, ast.Name) and node.id == "JsonValue":
                # Report the type/import once, but do not duplicate the Name
                # that is merely the value inside a dict[...] annotation.
                parent = parents.get(node)
                if not (
                    isinstance(parent, ast.Subscript)
                    and isinstance(parent.value, ast.Name)
                    and parent.value.id in {"dict", "Dict"}
                ):
                    violations.append(f"{where} uses JsonValue")
            elif isinstance(node, ast.ImportFrom):
                imported = {alias.name for alias in node.names}
                if "JsonValue" in imported:
                    violations.append(f"{where} imports JsonValue")
                if "Dict" in imported:
                    violations.append(f"{where} imports the raw dictionary type")
    assert violations == [], "raw dictionaries in owned packages:\n  " + "\n  ".join(violations)


def test_no_canonical_payload_carries_a_presentation_field():
    """A canonical fact says what HAPPENED; how it is drawn is the renderers'.

    A payload that grew an `html` or an `ansi` field would put one surface's
    styling into the store every other surface reads, permanently — the
    canonical schema is append-only, so the field could never be taken back out.

    This is a property of twelve dataclass DECLARATIONS, and it used to be
    checked by a loop over every registered payload, re-run on every stored
    event ever built, to answer a question that cannot change while the
    process is running. It belongs in the suite that reads the tree, and this
    is that suite.
    """
    forbidden = frozenset(
        {
            "ansi",
            "bubbled",
            "chrome",
            "css",
            "glyph",
            "gutter",
            "html",
            "note",
            "rgb",
            "web",
            "wrap",
        }
    )
    carrying = [
        f"{payload_type.__name__} carries {sorted(found)!r}"
        for payload_type in EVENT_TYPES
        if (found := forbidden.intersection(field.name for field in fields(payload_type)))
    ]
    assert carrying == []


def test_claude_otel_is_not_a_top_level_harness():
    """OTLP is one harness's side channel, not a harness.

    What an export MEANS stays under the plugin that reports it; the endpoint that
    receives one is a client (`client/claude_otel.py`), spawned by the launcher
    beside that gateway.
    """
    assert not (ROOT / "harness" / "impl" / "otel").exists()
    assert (ROOT / "harness" / "impl" / "claude_code" / "otel" / "gateway.py").is_file()
    assert (ROOT / "harness" / "impl" / "claude_code" / "otel" / "launch.py").is_file()


def test_canonical_shared_code_imports_no_concrete_harness_package():
    canonical_shared_paths = [
        ROOT / "api",
        ROOT / "app",
        ROOT / "domain",
        ROOT / "engine",
        ROOT / "terminal",
        ROOT / "harness" / "contract.py",
        ROOT / "harness" / "registry.py",
        ROOT / "harness" / "models",
        ROOT / "harness" / "hooks",
        ROOT / "harness" / "services",
        ROOT / "dashboard" / "services",
    ]
    concrete_prefixes = ("harness.impl.claude_code", "harness.impl.codex")
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
        "claude_code": "harness.impl.codex",
        "codex": "harness.impl.claude_code",
    }
    importers = []
    for package_name, forbidden_prefix in forbidden_imports.items():
        for path in (ROOT / "harness" / "impl" / package_name).rglob("*.py"):
            for imported_path, imported in imports_under_path(path):
                if imported.startswith(forbidden_prefix):
                    importers.append(f"{imported_path.relative_to(ROOT)} imports {imported}")
    assert importers == []


def test_canonical_shared_code_contains_no_concrete_harness_vocabulary():
    shared_paths = [
        ROOT / "api",
        ROOT / "app",
        ROOT / "core",
        ROOT / "dashboard",
        ROOT / "domain",
        ROOT / "engine",
        ROOT / "terminal",
        ROOT / "harness" / "contract.py",
        ROOT / "harness" / "registry.py",
        ROOT / "harness" / "models",
        ROOT / "harness" / "hooks",
        ROOT / "harness" / "services",
    ]
    concrete_words = ("claude", "codex", "anthropic", "openai", "rollout", "transcript")
    violations = []
    for shared_path in shared_paths:
        paths = shared_path.rglob("*.py") if shared_path.is_dir() else (shared_path,)
        for path in paths:
            # The requested closed HarnessName enum is the single owner of the
            # installed adapter names. No other shared module may spell them.
            if path == ROOT / "domain" / "ids.py":
                continue
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
    for directory in ("app", "engine", "dashboard", "terminal", "core", "harness", "audit", "notify"):
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
                violations.append(f"{path.relative_to(ROOT)} contains abbreviated {abbreviation}")
    assert violations == []


def test_session_lifecycle_has_no_per_harness_implementation():
    """Pane open/close is harness-agnostic and lives in the interpreter's react
    step; a reappearing per-plugin lifecycle module means the split regressed."""
    assert not (ROOT / "harness" / "impl" / "codex" / "lifecycle.py").exists()
    assert not (ROOT / "harness" / "impl" / "claude_code" / "lifecycle.py").exists()


def test_the_launch_wrappers_are_gone():
    """Launching is just running the CLI.

    The recorder that used to build the application graph — the OTLP receiver —
    is a stdlib-only client now, and that it builds nothing is checked where it
    lives (tests/test_canonical_clients.py). What remains here is the absence of
    the two wrappers that once wrapped a launch.
    """
    assert not (ROOT / "harness" / "impl" / "claude_code" / "command.py").exists()
    assert not (ROOT / "harness" / "impl" / "codex" / "command.py").exists()


FILE_ACCESS_ALLOWLIST = {
    # --- credentials: user-installed secrets we trade, never own ---------------
    "dashboard/dictate.py": "the Deepgram API key and keyterms",
    "notify/channels/telegram.py": "the bot token and chat id",
    # --- source files: written by a harness, or authored by you ---------------
    "harness/impl/claude_code/canonical/transcript.py": (
        "the transcript — read as evidence, appended to for a parked rename"
    ),
    "harness/impl/claude_code/canonical/sources.py": "transcripts and task files, read as evidence",
    "harness/impl/claude_code/canonical/messages.py": "a child actor's meta.json sidecar, read as evidence",
    "harness/impl/claude_code/controls/controller.py": "reads the transcript tail to confirm an interrupt landed",
    "harness/impl/claude_code/model.py": "the agent meta.json sidecar beside a transcript",
    "harness/impl/claude_code/slashcmds.py": "your .claude/commands and skills",
    "harness/impl/claude_code/hooks/foreground.py": "creates the tee file a command writes its output into",
    "harness/impl/claude_code/shell.py": "the tee file's directory",
    "harness/file_tail.py": "the common append-only harness source reader",
    "harness/impl/codex/canonical/rollout.py": "a subagent rollout's replayed-parent prefix, measured on the file",
    "harness/impl/codex/canonical/sources.py": "rollouts, read as evidence",
    "harness/impl/codex/canonical/translator.py": "backscans a rollout for the collaboration call an activity resolves",
    "harness/impl/codex/canonical/title.py": "globs codex's own state index",
    "harness/impl/codex/commands.py": "your $CODEX_HOME/prompts",
    "harness/impl/codex/controls/controller.py": "reads the rollout tail to confirm an interrupt landed",
    "harness/impl/__init__.py": "plugin discovery globs its own directory",
    "harness/services/usage.py": "a run-scoped cross-process usage cache and its lock",
    # --- ours, and the one place we write bytes rather than rows ---------------
    "api/application/files.py": "stages an attachment; the harness is handed an @path",
    "engine/interpret/output_source.py": "reads a followed output file, and unlinks the tee we made",
    "core/clipboard.py": "the host pasteboard",
    "core/repository.py": "reads a .git file to resolve a worktree",
    "core/process.py": "/proc-style process inspection",
    "terminal/impl/kitty/remote.py": "finds the terminal's control SOCKET, not a file",
    "dashboard/paths.py": "resolves the uploads directory",
    "dashboard/cli.py": "--log sends the daemon's own output to a file",
    "api/application/static.py": "serves the SPA's own files",
    "dashboard/frontend_build.py": "validates Vite's generated manifest and source stamp",
    "bin/retarget-python.py": "rewrites hook shebangs and the user hook configuration",
    "client/_handoff.py": "shares pane state with short-lived terminal click handlers",
    "inference/default.py": "writes the output schema required by the Codex CLI",
}


def test_no_module_outside_the_allowlist_reads_or_writes_a_file():
    """Everything we own is a row. The exceptions are named, with their reason.

    Two classes survive: CREDENTIALS the user installs and we only trade, and
    SOURCE FILES a harness writes or you author — a transcript, a rollout, a
    slash-command definition. Both are things we do not own. The third entry is
    the one place we write bytes rather than a row, and even that now has a row
    beside it: an attachment reaches the harness as an `@path`, so a real file
    has to exist.
    """
    # Word-boundary matched, so `urlopen(` — which is a socket, not a file —
    # does not read as one.
    markers = (
        r"\bopen\(",
        r"\bos\.makedirs\b",
        r"\bos\.listdir\b",
        r"\bos\.scandir\b",
        r"\bglob\.glob\b",
        r"\.write_text\(",
        r"\.write_bytes\(",
        r"\.read_text\(",
    )
    violations = []
    for package in OUR_PACKAGES:
        for path in sorted((ROOT / package).rglob("*.py")):
            relative = path.relative_to(ROOT).as_posix()
            if relative in FILE_ACCESS_ALLOWLIST:
                continue
            if relative.startswith("repository/impl/sqlite/"):
                continue  # makedirs for the database's own directory
            code = _code_only(path)
            found = [marker for marker in markers if re.search(marker, code)]
            if found:
                violations.append(f"{relative} contains {', '.join(found)}")
    assert violations == []


def test_the_file_access_allowlist_has_no_stale_entries():
    """An allowlist may not outlive its reason — the same rule the type ratchet has."""
    stale = [relative for relative in FILE_ACCESS_ALLOWLIST if not (ROOT / relative).is_file()]
    assert stale == []


def test_only_the_daemon_and_the_audit_cli_build_repositories():
    """One process writes. Two others link the layer, and both are reasoned.

    The hook entries, the pane processes, the keybinding and the OTLP receiver are
    HTTP clients of the daemon; they may not import the contract, let alone an
    implementation, and they no longer CAN — they are files in `client/` that
    import nothing of ours (tests/test_canonical_clients.py).
    `app/raw_events_audit_cli.py` is the exception, because it is the tool you run when
    the daemon is the suspect, and it opens read-only. `audit/record.py` is
    the other, because the daemon's own boot and its request guard record before
    and outside the graph that would inject a repository.

    `api/server.py` and `dashboard/cli.py` used to be named here too, for the pid
    lock they shared. The daemon is a singleton because it binds a port, so
    neither one opens anything now: the CLI asks the port who is answering.
    """
    allowed_builders = {
        "app/providers.py",
        "app/raw_events_audit_cli.py",
        "audit/record.py",
    }
    builders = set()
    for package in OUR_PACKAGES:
        for path, imported in imports_under(package):
            relative = path.relative_to(ROOT).as_posix()
            if relative.startswith("repository/"):
                continue
            if imported.startswith("repository.impl"):
                builders.add(relative)
    assert builders == allowed_builders

    # The forensic reader may never open the file it inspects for writing.
    audit_cli = (ROOT / "app" / "raw_events_audit_cli.py").read_text(encoding="utf-8")
    assert "read_only(" in audit_cli

    # The nine thin clients that used to be named here one by one are a directory
    # now, and the rule about them is the whole of tests/test_canonical_clients.py:
    # they import nothing of ours, so "does it name a repository" is not a
    # question that can be asked of them any more.


def test_terminal_storage_is_reached_through_a_service():
    """A route is not its own service, and a renderer does not open a database.

    Two things that used to be here are gone entirely. The view toggle took with
    it the one route in the tree that was its own service — which files the mirror
    has expanded is the PANE's state now, because a file entry carries its own
    diff. And the live screen/keys passthrough went with it: everything a caller
    used it for is a fact in the read model or a control gesture of its own, so
    api/terminal/ is the pane keybindings and nothing else.
    """
    for name in ("api/terminal/panes.py", "terminal/panes/commands.py", "terminal/panes/reaction.py"):
        source = (ROOT / name).read_text(encoding="utf-8")
        # Not "names no repository": the pane reaction legitimately reads the
        # SESSION its panes anchor to. What it may not reach is the TERMINAL's
        # own storage — the widths and the opened views — which is what the two
        # services above own.
        assert "repository.contract.terminal" not in source, f"{name} reaches pane storage"
        assert "repository.impl" not in source, f"{name} names an implementation"
    source = (ROOT / "api" / "terminal" / "panes.py").read_text(encoding="utf-8")
    assert "repository." not in source, "api/terminal/panes.py is a route, not a service"


# test_hook_entries_are_thin_clients_of_the_daemon lived here and named four
# files by hand. Its rule — a hook ships its exact stdin to the daemon and neither
# builds the graph nor writes the store — is now
# tests/test_canonical_clients.py::test_clients_import_only_the_standard_library_and_their_siblings,
# which says the same thing about a whole directory and cannot be escaped by
# adding a fifth file.


def test_the_graph_is_declared_in_one_place_and_injected_everywhere():
    """One process interprets, and it does not assemble an object to do it.

    `app/providers.py` is the only module that declares an APPLICATION node:
    `@singleton` is its decorator, and every consumer — a route, a background
    thread, a test — asks for the node it uses. `api/dependencies.py` is the
    second and last, for the one node that is not the application's: the HTTP
    policy. It lives there because `app/` is the composition root and may not
    import the layer above it.

    The rule this replaced pinned the strings `build_application(` and
    `build_default_application` to two files, because the graph was ONE frozen
    33-field object handed to whoever needed a field of it. There is no such
    object to build now.
    """
    declarers = set()
    for directory in ("bin", *OUR_PACKAGES):
        for path in sorted((ROOT / directory).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            if "@singleton" in _code_only(path):
                declarers.add(path.relative_to(ROOT).as_posix())
    assert declarers == {"app/providers.py", "api/dependencies.py"}


def test_the_audit_floor_is_only_for_writers_with_no_graph():
    """Everything with a constructor takes `AuditRecorder`; the floor is the rest.

    `audit/record.py` is the same five writes over a repository nobody
    injected, and it exists for writers that genuinely cannot be handed one: the
    CLI verb that audits a spawn before the daemon exists, and free functions deep
    enough that passing a recorder would mean growing a parameter on every caller
    between here and there. Everything else — the interpreter, the control
    service, the pane commands, the notifier, every route — takes the node.

    Each entry is a decision, not a leftover. A new importer means either a class
    that should have taken the recorder, or a reason stated here.
    """
    floor = {
        "dashboard/cli.py",  # audits the spawn; the daemon is not up yet
        "core/clipboard.py",  # a free function on the host pasteboard
        "harness/impl/claude_code/controls/tui.py",  # a screen driver, below every service
        "notify/channels/__init__.py",  # channel dispatch: free functions
        "notify/channels/telegram.py",  # ...and the two channels behind it
        "notify/channels/webpush.py",
    }
    importers = set()
    for package in OUR_PACKAGES:
        for path in sorted((ROOT / package).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            relative = path.relative_to(ROOT).as_posix()
            if relative == "audit/record.py":
                continue
            if "from audit import record" in _code_only(path):
                importers.add(relative)
    assert importers == floor


def test_no_route_takes_the_whole_graph():
    """A route signature is its dependency list, or it is a lie.

    Every handler used to take one `ApplicationGraph` — the entire application —
    to reach one or two fields of it, and two of them read `app.state` by hand.
    Both spellings are gone: a handler names the services it uses, and the ONLY
    module that touches the singleton registry is the kernel that owns it.
    """
    banned = ("ApplicationGraph", "canonical_application", "app.state.instances")
    allowed = {"app/injection.py", "api/app.py"}
    violations = []
    for package in OUR_PACKAGES:
        for path in sorted((ROOT / package).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            relative = path.relative_to(ROOT).as_posix()
            if relative in allowed:
                continue
            source = _code_only(path)
            for name in banned:
                if name in source:
                    violations.append(f"{relative} names {name}")
    assert violations == []


def test_every_declared_node_resolves_and_resolves_once():
    """The declarations are a graph, not a list: it has to close.

    Resolving every provider proves each one's parameters name providers too
    (`app/injection.py` raises otherwise), and asking twice proves the scope is
    a singleton — the session list's warm cache and the interpreter are ONE
    object per application, or the daemon has two of each.
    """
    from app import providers as declared
    from app.injection import registry, resolve

    instances = registry()
    nodes = [name for name in dir(declared) if not name.startswith("_") and hasattr(getattr(declared, name), "build")]
    assert len(nodes) > 40, nodes
    for name in nodes:
        provider = getattr(declared, name)
        assert resolve(instances, provider) is resolve(instances, provider), name
    # A second registry is a second application: nothing is shared through the
    # module, which is the whole difference between a singleton and a global.
    other = registry()
    assert resolve(other, declared.main_db) is not resolve(instances, declared.main_db)


def test_claude_foreground_hook_has_no_legacy_drawing_or_state_dependency():
    implementation_files = (
        ROOT / "harness" / "impl" / "claude_code" / "hooks" / "foreground.py",
        ROOT / "harness" / "impl" / "claude_code" / "shell.py",
    )
    forbidden_roots = {"api", "app", "dashboard", "engine", "terminal"}
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
    assert not (ROOT / "harness" / "impl" / "claude_code" / "foreground_process.py").exists()


def test_canonical_consumers_cannot_observe_or_checkpoint_native_sources():
    consumers = [
        # The two pane processes were on this list, and so were the daemon-side
        # renderers that fed them. Both are gone: a pane imports nothing of ours
        # at all now, which is a stronger version of this rule and is checked in
        # tests/test_canonical_clients.py.
        ROOT / "dashboard" / "services",
        ROOT / "engine" / "sessiondata",
        *sorted((ROOT / "api").rglob("*.py")),
    ]
    forbidden_fragments = (
        ".drain(",
        ".sources(",
        "CheckpointStore",
        "ObservationRunner",
        "SourceCheckpoint",
    )
    violations = []
    for consumer in consumers:
        for path in sorted(consumer.rglob("*.py")) if consumer.is_dir() else (consumer,):
            source = path.read_text(encoding="utf-8")
            found = [fragment for fragment in forbidden_fragments if fragment in source]
            if found:
                violations.append(f"{path.relative_to(ROOT)} contains {', '.join(found)}")
    assert violations == []


def test_canonical_sse_has_no_broker_or_application_event_registry():
    # One file now: both surfaces are the same poll-and-diff loop over the read
    # model, and the pane streams died with the daemon's renderer.
    source = (ROOT / "api" / "sessiondata" / "streams.py").read_text(encoding="utf-8")
    assert "DashboardEventStream" not in source
    assert "subscribe" not in source
    assert "queue.Queue" not in source
    assert not (ROOT / "dashboard" / "events.py").exists()
    assert not (ROOT / "api" / "broker.py").exists()


def test_global_application_updates_use_the_event_stream_instead_of_a_timer():
    state = (
        ROOT / "dashboard" / "frontend" / "src" / "app" / "app-state.svelte.ts"
    ).read_text(encoding="utf-8")
    stream = (
        ROOT / "dashboard" / "frontend" / "src" / "api" / "global-stream.ts"
    ).read_text(encoding="utf-8")

    assert "APPLICATION_REFRESH_MS" not in state
    assert "applicationRefreshTimer" not in state
    assert "addEventListener('application'" in stream


def test_resume_and_sse_have_one_authoritative_path():
    launch_files = (
        ROOT / "harness" / "models" / "launch.py",
        ROOT / "api" / "controls" / "routes.py",
        ROOT / "dashboard" / "frontend" / "src" / "sessions" / "components" / "Composer.svelte",
        ROOT / "dashboard" / "frontend" / "src" / "new-session" / "NewSessionModal.svelte",
        ROOT / "dashboard" / "frontend" / "src" / "app" / "app-state.svelte.ts",
        ROOT / "harness" / "impl" / "claude_code" / "launcher.py",
        ROOT / "harness" / "impl" / "codex" / "launcher.py",
    )
    assert not [
        path.relative_to(ROOT) for path in launch_files if "continue_latest" in path.read_text(encoding="utf-8")
    ]
    session_browser = (ROOT / "dashboard" / "frontend" / "src" / "api" / "session-stream.ts").read_text(
        encoding="utf-8"
    )
    assert "stream.onerror" not in session_browser
    assert "SES_RECONNECT" not in session_browser


def test_harness_packages_are_only_inert_package_markers():
    """A harness package's `__init__.py` never runs anything.

    Discovery imports `<harness>/plugin.py` directly, so an `__init__` that did
    work would make merely NAMING a harness (a test, an audit CLI) pay for it —
    and would run before the descriptor the registry validates. `harness/impl/
    __init__.py` is exempt: it IS the discovery door, the twin of
    `terminal/impl/__init__.py`.
    """
    for package_path in (
        ROOT / "harness" / "impl" / "claude_code" / "__init__.py",
        ROOT / "harness" / "impl" / "codex" / "__init__.py",
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
    # the HTTP tier itself moved out of the presenter package entirely (api/)
    assert not (ROOT / "dashboard" / "http").exists()
    assert not (ROOT / "dashboard" / "server.py").exists()
    assert not (ROOT / "harness" / "impl" / "host.py").exists()
    assert not (ROOT / "harness" / "impl" / "claude_code" / "hostctl.py").exists()
    assert not (ROOT / "harness" / "impl" / "codex" / "hostctl.py").exists()
    assert not list((ROOT / "dashboard" / "ext").rglob("*.py"))
    assert not list((ROOT / "dashboard" / "opshtml").glob("*.py"))


def test_descriptor_discovery_does_not_load_legacy_semantic_stores():
    program = """
import sys
from harness.impl import installed
installed()
forbidden = {'core.ops', 'core.state', 'core.sessionapi', 'harness.impl.host'}
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

SOURCE_PACKAGES = ("app", "core", "dashboard", "audit", "domain", "harness", "engine", "notify", "terminal")

# Structural implementers that must NOT declare their Protocol, with the reason.
# Both are the same shape: the Protocol is declared in a layer that sits BELOW
# the implementer, so importing it to declare it would invert the dependency —
# the dashboard names the shape it needs from the application, and the
# application must not import the dashboard to say "yes, that is me". Moving
# these two Protocols into `harness/models/` would retire both rows.
PROTOCOL_DECLARATION_EXEMPTIONS = {
    ("TerminalAdapter", "SessionTerminalState"): "the Protocol lives in harness/, which terminal/ may not import",
    ("TerminalInputService", "TerminalSessionReader"): "the Protocol lives in dashboard/, which app/ may not import",
    ("ApplicationUsageState", "UsageReader"): "the Protocol lives in dashboard/, which harness/ may not import",
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
                is_protocol = any(base == "Protocol" or base.startswith("Protocol[") for base in bases)
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
                        f"{where} {name}.{member}{members[member]} does not match {protocol}.{member}{arguments}"
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


# --- Control handlers ---------------------------------------------------------
#
# The handlers registered in a HarnessController are CLASSES that declare
# ControlHandler, so the two tests above already hold their signatures to the
# Protocol. They were plain functions until this change, and a function
# subclasses nothing -- so nothing checked their shape at all, which made them
# the largest unguarded surface in the contract.
#
# What is left to check is the WIRING, which no type carries: that every
# registered value really is such a class, and that every key is a real control
# name. A typo registers a handler nothing will ever call, in silence.


def _control_name_values() -> dict[str, str]:
    """Every `ControlName` member, as member-name -> its string value.

    `ControlName` is a `StrEnum` class now, not a `Literal` type alias — each
    member is an `Assign` in the class body, `MEMBER = "value"`.
    """
    tree = ast.parse((ROOT / "harness" / "models" / "controls.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "ControlName":
            values = {}
            for statement in node.body:
                if (
                    isinstance(statement, ast.Assign)
                    and len(statement.targets) == 1
                    and isinstance(statement.targets[0], ast.Name)
                    and isinstance(statement.value, ast.Constant)
                    and isinstance(statement.value.value, str)
                ):
                    values[statement.targets[0].id] = statement.value.value
            return values
    raise AssertionError("ControlName is missing")


def _control_names() -> set[str]:
    return set(_control_name_values().values())


def _registered_handlers():
    """(where, control_name, handler_class_name, declared_bases) per registration."""
    control_name_values = _control_name_values()
    for path in (ROOT / "harness" / "impl").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        classes = {
            node.name: [ast.unparse(base) for base in node.bases]
            for node in tree.body
            if isinstance(node, ast.ClassDef)
        }
        mappings = {
            target.id: node.value
            for node in tree.body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and isinstance(node.value, ast.Dict)
            for target in (node.target,)
        }
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "HarnessController"):
                continue
            for mapping in node.args:
                if isinstance(mapping, ast.Name):
                    mapping = mappings.get(mapping.id, mapping)
                if not isinstance(mapping, ast.Dict):
                    continue
                for key, value in zip(mapping.keys, mapping.values):
                    where = f"{path.relative_to(ROOT)}:{key.lineno}"
                    name = getattr(getattr(value, "func", None), "id", None)
                    # A key is either the old bare string or `ControlName.MEMBER`
                    # (an Attribute whose `.attr` names the member) — resolved to
                    # the same string either way, so a rename of the enum's
                    # member names is still checked against the real vocabulary.
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        control_name = key.value
                    elif isinstance(key, ast.Attribute):
                        control_name = control_name_values.get(key.attr, key.attr)
                    else:
                        control_name = ast.unparse(key)
                    yield where, control_name, name, classes.get(name)


def test_every_registered_control_handler_declares_the_protocol():
    wrong = []
    registrations = list(_registered_handlers())
    for where, control_name, name, bases in registrations:
        if name is None:
            wrong.append(f"{where} {control_name!r} is not registered as a handler instance")
        elif bases is None:
            wrong.append(f"{where} handler {name!r} is not a class in this module")
        elif "ControlHandler" not in bases:
            wrong.append(f"{where} {name} does not declare ControlHandler (bases={bases})")
    assert wrong == []
    # a controller that registered nothing would satisfy every assertion above
    assert len(registrations) > 20


def test_every_registered_control_name_is_a_real_one():
    names = _control_names()
    unknown = [
        f"{where} registers unknown control {control_name!r}"
        for where, control_name, _name, _bases in _registered_handlers()
        if control_name not in names
    ]
    assert unknown == []


def test_shared_models_do_not_expose_adapter_identity_fields():
    """Vendor handles stay behind their adapter boundary.

    A session is canonically identified by SessionId, and a model fact carries
    a portable name. Reintroducing a second harness/native/selection identity
    here would make every adapter pretend to support another adapter's concept.
    """
    from domain.values import ModelReference
    from harness.models import Session

    assert set(Session.__dataclass_fields__) == {
        "session_id",
        "lead_actor_id",
        "source_reference",
        "working_directory",
        "terminal_window_id",
        "harness_process_id",
        "plugin",
        "project_directory",
    }
    assert set(ModelReference.__dataclass_fields__) == {"name", "display_name"}


def test_adapter_identity_types_do_not_live_in_domain_ids():
    source = (ROOT / "domain" / "ids.py").read_text(encoding="utf-8")
    forbidden = ("HarnessSessionId", "ModelId", "SelectionId", "ShellNativeId", "CallId")
    assert [name for name in forbidden if name in source] == []


def test_adapter_identity_types_are_owned_and_prefixed_by_their_adapter():
    for adapter, prefix in (("claude_code", "ClaudeCode"), ("codex", "Codex")):
        path = ROOT / "harness" / "impl" / adapter / "ids.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        declared = [
            target.id
            for node in tree.body
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and getattr(node.value.func, "id", None) == "NewType"
            for target in node.targets
            if isinstance(target, ast.Name)
        ]
        assert declared
        assert [name for name in declared if not name.startswith(prefix)] == []


def test_adapters_map_native_entity_ids_only_in_their_ids_module():
    """An adapter may consume domain IDs, but it may not mint them ad hoc.

    Keeping every native-to-canonical conversion in ``impl/<adapter>/ids.py``
    makes the namespace crossing explicit and prevents a vendor call, turn, or
    process handle from becoming a domain identity by an incidental cast.
    Infrastructure IDs (account/window inputs supplied by Baqylau) are
    deliberately outside this list: they enter the adapter in domain form.
    """
    canonical_entity_ids = {
        "ActorId",
        "AssignmentId",
        "AttentionId",
        "MessageId",
        "QuestionId",
        "ReasoningId",
        "SessionId",
        "ShellId",
        "SkillId",
        "TaskId",
        "TaskListId",
        "TurnId",
    }
    violations = []
    for adapter in ("claude_code", "codex"):
        root = ROOT / "harness" / "impl" / adapter
        for path in root.rglob("*.py"):
            if path == root / "ids.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                    continue
                if node.func.id in canonical_entity_ids:
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno} constructs {node.func.id}")
    assert violations == []

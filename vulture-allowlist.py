# Vulture allowlist — names reached by a protocol, never by a caller in this
# repo. Permanent: every entry below is a framework/stdlib contract, not debt.
# (Dead code that merely HASN'T been deleted yet lives in vulture-baseline.py.)
#
# Nothing here is imported or executed; vulture parses the file and counts each
# name as one use. Framework CALL SITES that ride a decorator (FastAPI routes,
# pydantic validators) are handled by --ignore-decorators in the Makefile.

# http.server dispatches these by name on the handler class.
_.log_message
_.do_POST

# HTTPServer.server_bind() assigns them; the stdlib reads them back.
_.server_name
_.server_port

# sqlite3.Connection attribute — set to shape rows, never read by us.
_.row_factory

# inspect/FastAPI contract: `singleton` rewrites the signature the framework
# reads off a provider, so the attribute is assigned here and read by
# `inspect.signature` — never by a caller of ours.
_.__signature__

# The account pair a launched CLI carries in its environment. Read by a program
# that CANNOT import this one — `client/_http.py`, which imports nothing of ours
# by design — so the only reference inside the import graph is the module that
# owns the concept and validates what comes back.
# tests/test_canonical_clients.py::test_the_http_module_matches_the_daemon pins the two
# copies to each other, so this is a contract with a reader, not dead code.
SLUG_VARIABLE
LABEL_VARIABLE

# --- response-model fields reached only by serialization ------------------------
# Each of these is a frozen-dataclass field the browser reads, carried out by
# `dashboard.render.serialize.json_ready`'s `getattr(value, field.name)` fan-out.
# Vulture cannot follow that, and the three below stopped looking used only
# because the last non-serialization reader of the NAME went away with the code
# this refactor deleted. They belong here rather than in the shrink-only
# baseline: a serialized field is a framework contract, not debt.
new_session_drafts  # dashboard/services/preferences.py
error_id         # audit/models.py

# anyio's capacity limiter: we ASSIGN the pool size the policy asked for and
# anyio reads it back when it hands out worker threads (api/app.py's lifespan).
# The only reference of ours is the assignment, which is what a framework
# attribute looks like from inside our own graph.
_.total_tokens

# A TypedDict field read only through dict-literal construction and ["check"]
# subscripts (askdialog_screen.rows / its dialog callers) — the ANNOTATION is
# the only bare-name mention, which is what an unused variable looks like to
# vulture. The field is the checkbox state the multi-select dialog reads.
check  # harness/impl/claude_code/controls/askdialog_screen.py Row

# Three more TypedDict fields with the same shape as `check` above: the typed
# migration gave dict-shaped returns their real field types, and each field's
# only bare-name mention is its annotation — readers go through ["…"]
# subscripts and dict literals, which vulture cannot connect to the name.
access_token  # dashboard/dictate.py GrantResponse
alias         # harness/impl/claude_code/account.py AccountRecord
decided       # harness/impl/claude_code/controls/plandialog.py Decided

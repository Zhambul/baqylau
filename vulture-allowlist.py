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

# --- response-model fields reached only by serialization ------------------------
# Each of these is a frozen-dataclass field the browser reads, carried out by
# `dashboard.render.serialize.json_ready`'s `getattr(value, field.name)` fan-out.
# Vulture cannot follow that, and the three below stopped looking used only
# because the last non-serialization reader of the NAME went away with the code
# this refactor deleted. They belong here rather than in the shrink-only
# baseline: a serialized field is a framework contract, not debt.
file_path        # dashboard/render/items/item.py
new_session_drafts  # dashboard/services/overview.py
error_id         # diagnostics/models.py

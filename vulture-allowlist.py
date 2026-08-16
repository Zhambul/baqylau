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

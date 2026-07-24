# dashboard/opshtml/ — the WEB presenter of the mirror's paint-op vocabulary.
#
# Split by concern (docs/architecture.md): ansi.py (ANSI/SGR -> HTML + the
# html.escape() security core, the neutralize() analog), ops.py (op shapes ->
# HTML blocks), markdown.py (the safe Markdown subset), tools.py (Claude's
# built-in tool payloads). This __init__ re-exports the stable public surface so
# `dashboard.opshtml.<name>` keeps resolving for read.mirror / read.session /
# notehtml / the tests.
from dashboard.opshtml.ansi import ansi_html, text_presentation  # noqa: F401
from dashboard.opshtml.markdown import md_html  # noqa: F401
from dashboard.opshtml.ops import op_html, op_items, ops_html, view_html  # noqa: F401
from dashboard.opshtml.tools import (  # noqa: F401
    answer_html, msg_html, tool_html, tool_output_html, WRITE_CAP,
)

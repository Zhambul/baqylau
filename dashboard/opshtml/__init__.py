# dashboard/opshtml/ — the WEB presenter of the mirror's paint-op vocabulary.
#
# Split by concern (docs/architecture.md): ansi.py (ANSI/SGR -> HTML + the
# html.escape() security core, the neutralize() analog), ops.py (op shapes ->
# HTML blocks), actclass.py (an op -> its activity class, what the view modes
# collapse on), markdown.py (the safe Markdown subset), tools.py (Claude's
# built-in tool payloads). This __init__ re-exports the stable public surface so
# `dashboard.opshtml.<name>` keeps resolving for read.mirror / read.session /
# notehtml / the tests.
from dashboard.opshtml.actclass import ACT_MSG, ACTS, classify  # noqa: F401
from dashboard.opshtml.ansi import ansi_html, text_presentation  # noqa: F401
from dashboard.opshtml.markdown import md_html  # noqa: F401
from dashboard.opshtml.ops import (  # noqa: F401
    in_scope, op_html, op_items, ops_html, view_html)
from dashboard.opshtml.tools import (  # noqa: F401
    answer_html, msg_html, tool_html, tool_output_html, WRITE_CAP,
)

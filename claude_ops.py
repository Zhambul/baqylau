#!/Users/z.yermagambet/.pyenv/versions/3.12.1/bin/python3
# claude_ops.py — compat AGGREGATOR. The old grab-bag split along the
# architecture seams (docs/architecture.md):
#
#   core/ops.py                        paint ops, emit, bump/counters,
#                                      scoreboard/token parts, semantic colours
#   plugins/claude_code/accounting.py  PRICES, cost_usd, usage_* dedup fold,
#                                      fold_usage, bump_transcript
#   plugins/claude_code/tools.py       parse_redirect, diff_counts, read_extent,
#                                      edit_range, FILE_LABEL/FILE_RGB
#   plugins/claude_code/hookkit.py     log_path (payload -> mirror-log key)
#   plugins/claude_code/model.py       claude_dirs (ancestor-.claude walking)
#
# Unlike the other shims this is a NAMESPACE COPY, not a sys.modules redirect —
# there is no single home module to alias to. `import claude_ops as O` keeps
# every historical name working (incl. O.A).
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.ops import *                                  # noqa: F401,F403
from core.ops import A                                  # noqa: F401
from plugins.claude_code.accounting import *            # noqa: F401,F403
from plugins.claude_code.tools import *                 # noqa: F401,F403
from plugins.claude_code.hookkit import log_path        # noqa: F401
from plugins.claude_code.model import claude_dirs       # noqa: F401

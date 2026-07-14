#!/Users/z.yermagambet/.pyenv/versions/3.12.1/bin/python3
# claude_tail.py — compat shim: the implementation moved to core/tail.py
# (docs/architecture.md). `import claude_tail` yields that same module object.
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import tail as _impl
sys.modules[__name__] = _impl

#!/Users/z.yermagambet/.pyenv/versions/3.12.1/bin/python3
# claude_render.py — compat shim: the implementation moved to core/render.py
# (README § Architecture). `import claude_render` yields that same module object.
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import render as _impl
sys.modules[__name__] = _impl

#!/Users/z.yermagambet/.pyenv/versions/3.12.1/bin/python3
# claude_msgs.py — compat shim: the implementation moved to plugins/claude_code/msgs.py
# (README § Architecture). `import claude_msgs` yields that same module object.
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plugins.claude_code import msgs as _impl
sys.modules[__name__] = _impl

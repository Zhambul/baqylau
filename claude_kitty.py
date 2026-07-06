#!/Users/z.yermagambet/.pyenv/versions/3.12.1/bin/python3
# claude_kitty.py — compat shim: the implementation moved to frontends/kitty.py
# (README § Architecture). `import claude_kitty` yields that same module object
# (module-level helpers preserved: find_kitten, kitten_run, kitten_ls,
# iter_windows, window_for_session, set_tab_color — plus KittyFrontend).
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from frontends import kitty as _impl
sys.modules[__name__] = _impl

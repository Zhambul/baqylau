#!/Users/z.yermagambet/.pyenv/versions/3.12.1/bin/python3
# claude-copy.py — entry point; the implementation lives in core/copy.py
# (docs/architecture.md). This filename is load-bearing: kitty's
# ~/.config/kitty/open-actions.conf `protocol claude-copy` rule invokes it with
# the clicked URL, and argv[0] is the audit DB's handler/script vocabulary.
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import copy as C
if __name__ == "__main__":
    C.entry(sys.argv)

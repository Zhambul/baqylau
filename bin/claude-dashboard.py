#!/usr/bin/env python3
# claude-dashboard.py [serve|start|stop|status|open] — entry point; the CLI
# lifecycle implementation lives in dashboard/cli.py (docs/architecture.md).
# This filename is load-bearing: `start` re-spawns it by name and argv[0] is the
# audit DB's spawn vocabulary.
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root (this file lives in bin/)
from dashboard import cli
if __name__ == "__main__":
    sys.exit(cli.main(sys.argv))

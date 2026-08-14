#!/usr/bin/env python3
"""Baqylau dashboard process entry."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard import cli

if __name__ == "__main__":
    raise SystemExit(cli.main(sys.argv))

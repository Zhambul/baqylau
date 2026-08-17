#!/usr/bin/env python3
"""Inspect raw harness evidence and its canonical interpretations."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.raw_events_audit_cli import main


if __name__ == "__main__":
    raise SystemExit(main())

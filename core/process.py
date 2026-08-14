"""Process liveness used by application singleton ownership."""

from __future__ import annotations

import os


def process_is_alive(process_id: int) -> bool:
    if process_id <= 0:
        return False
    try:
        os.kill(process_id, 0)
        return True
    except PermissionError:
        return True
    except ProcessLookupError:
        return False

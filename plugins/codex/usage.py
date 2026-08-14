"""Read Codex account rate limits from its app server."""

from __future__ import annotations

import json
import os
import subprocess
import time

REQUEST_TIMEOUT_SECONDS = 6.0
CACHE_SECONDS = 120.0
BINARY_DIRECTORIES = (
    "~/.hermes/node/bin",
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "~/.local/bin",
)

_cached_rate_limits: tuple[float, dict | None] | None = None


def subprocess_environment() -> dict[str, str]:
    environment = dict(os.environ)
    directories = []
    configured_directory = environment.get("CODEX_BIN_DIR")
    if configured_directory and os.path.isdir(configured_directory):
        directories.append(configured_directory)
    for candidate in BINARY_DIRECTORIES:
        directory = os.path.expanduser(candidate)
        if os.path.isdir(directory) and directory not in directories:
            directories.append(directory)
    if directories:
        directories.append(environment.get("PATH", ""))
        environment["PATH"] = os.pathsep.join(directories)
    return environment


def request_rate_limits() -> dict | None:
    try:
        process = subprocess.Popen(
            ["codex", "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env=subprocess_environment(),
        )
    except OSError:
        return None
    try:
        if process.stdin is None or process.stdout is None:
            return None
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"clientInfo": {"name": "baqylau", "version": "1"}},
        }
        request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "account/rateLimits/read",
            "params": {},
        }
        process.stdin.write(json.dumps(initialize) + "\n")
        process.stdin.write(json.dumps(request) + "\n")
        process.stdin.flush()
        deadline = time.time() + REQUEST_TIMEOUT_SECONDS
        while time.time() < deadline:
            line = process.stdout.readline()
            if not line:
                return None
            try:
                response = json.loads(line)
            except json.JSONDecodeError:
                continue
            if response.get("id") == 2:
                result = response.get("result")
                return result if isinstance(result, dict) else None
        return None
    finally:
        if process.stdin is not None:
            process.stdin.close()
        process.terminate()


def normalize_rate_limits(response: dict | None) -> dict | None:
    rate_limits = response.get("rateLimits") if isinstance(response, dict) else None
    if not isinstance(rate_limits, dict):
        return None
    windows = []
    for slot_name in ("primary", "secondary"):
        window = rate_limits.get(slot_name)
        if not isinstance(window, dict):
            continue
        used_percent = window.get("usedPercent")
        duration_minutes = window.get("windowDurationMins")
        if not isinstance(used_percent, (int, float)) or not isinstance(duration_minutes, (int, float)):
            continue
        windows.append({
            "used_percent": used_percent,
            "duration_minutes": int(duration_minutes),
            "resets_at": window.get("resetsAt"),
        })
    if not windows:
        return None
    return {"plan": rate_limits.get("planType") or "", "windows": tuple(windows)}


def read_rate_limits() -> dict | None:
    global _cached_rate_limits
    now = time.time()
    if _cached_rate_limits is not None and _cached_rate_limits[0] > now:
        return _cached_rate_limits[1]
    result = normalize_rate_limits(request_rate_limits())
    _cached_rate_limits = (now + CACHE_SECONDS, result)
    return result

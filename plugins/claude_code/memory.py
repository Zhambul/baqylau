"""Claude Code memory-vault vocabulary and document access."""

from __future__ import annotations

import os
import re
import time

HITS_MAX = 12
SNIPPET_CAP = 1200
MARK = "❖"
NOTE_EXT = (".md", ".markdown")
_DEFAULT_ROOT = "~/wiki/01"
_DEFAULT_PROJECT = "~/code/01/aggregator-adapters"
_READ_CAP = 256 * 1024
_INDEX_TTL_SECONDS = 30.0
_LINK_PATTERN = re.compile(r"\[\[\s*([^\]|#]+?)\s*(?:[#|][^\]]*)?\]\]")
_NAME_INDEX = {}
_BACKLINK_INDEX = {}


def root() -> str:
    return os.path.abspath(
        os.path.expanduser(os.environ.get("BAQYLAU_MEMORY_ROOT") or _DEFAULT_ROOT)
    )


def is_memory(path: str) -> bool:
    return bool(path) and os.path.abspath(path).startswith(root() + os.sep)


def rel(path: str) -> str:
    if not is_memory(path):
        return ""
    return os.path.relpath(os.path.abspath(path), root()).replace(os.sep, "/")


def project() -> str:
    return os.path.abspath(
        os.path.expanduser(
            os.environ.get("BAQYLAU_MEMORY_PROJECT") or _DEFAULT_PROJECT
        )
    )


def in_scope(working_directory: str | None = None) -> bool:
    directory = os.path.abspath(working_directory or os.getcwd())
    project_directory = project()
    return directory == project_directory or directory.startswith(project_directory + os.sep)


def _scan_names() -> dict[str, str]:
    paths = {}
    for directory, directory_names, filenames in os.walk(root()):
        directory_names[:] = [
            name for name in directory_names if name not in (".obsidian", ".git")
        ]
        for filename in filenames:
            if filename.endswith(".md"):
                paths.setdefault(filename[:-3], os.path.join(directory, filename))
    return paths


def _scan_backlinks() -> dict[str, tuple[str, ...]]:
    links = {}
    for source_stem, path in _scan_names().items():
        try:
            with open(path, encoding="utf-8", errors="replace") as document:
                body = document.read(_READ_CAP)
        except OSError:
            continue
        for match in _LINK_PATTERN.finditer(body):
            links.setdefault(match.group(1).strip(), set()).add(source_stem)
    return {stem: tuple(sorted(sources)) for stem, sources in links.items()}


def _cached(cache: dict, build):
    base = root()
    entry = cache.get(base)
    current_time = time.time()
    if entry and current_time - entry[0] < _INDEX_TTL_SECONDS:
        return entry[1]
    value = build()
    cache[base] = (current_time, value)
    return value


def resolve(stem: str | None) -> str | None:
    if not stem:
        return None
    return _cached(_NAME_INDEX, _scan_names).get(stem.strip())


def backlinks(path: str) -> tuple[str, ...]:
    if not path:
        return ()
    stem = os.path.basename(path.rstrip("/"))
    if stem.endswith(".md"):
        stem = stem[:-3]
    return _cached(_BACKLINK_INDEX, _scan_backlinks).get(stem, ())


def read_note(path: str | None) -> tuple[dict | None, str | None]:
    if not path or not is_memory(path):
        return None, None
    try:
        with open(path, encoding="utf-8", errors="replace") as document:
            text = document.read(_READ_CAP)
    except OSError:
        return None, None
    return _split_frontmatter(text)


def _split_frontmatter(text: str) -> tuple[dict, str]:
    frontmatter = {}
    body = text
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            block = text[4:end]
            newline = text.find("\n", end + 1)
            body = text[newline + 1:] if newline != -1 else ""
            for line in block.split("\n"):
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                if key.strip():
                    frontmatter[key.strip()] = value.strip()
    return frontmatter, body

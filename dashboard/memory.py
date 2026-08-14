"""Dashboard presentation for the typed memory application service."""

from __future__ import annotations

import html
import os
import re
from dataclasses import asdict, dataclass

from app.memory import MemoryService
from contracts.harness import MemoryDocument, MemoryNoteRecord
from dashboard.markdown import md_html
from domain.ids import SessionId

_LINK_PATTERN = re.compile(r"\[\[\s*([^\]|#]+?)\s*(?:#[^\]|]*)?(?:\|\s*([^\]]+?)\s*)?\]\]")
_SENTINEL_PATTERN = re.compile("\x02(\\d+)\x02")


@dataclass(frozen=True)
class DashboardMemorySnapshot:
    notes: tuple[dict, ...]
    tree: dict
    searches: tuple[dict, ...]


@dataclass(frozen=True)
class DashboardMemoryDocument:
    name: str
    path: str
    frontmatter: tuple[tuple[str, str], ...]
    html: str
    backlinks: tuple[str, ...]
    missing: bool


class DashboardMemoryService:
    def __init__(self, memory: MemoryService) -> None:
        self.memory = memory

    def snapshot(self, session_id: SessionId) -> DashboardMemorySnapshot:
        snapshot = self.memory.snapshot(session_id)
        return DashboardMemorySnapshot(
            notes=tuple(asdict(note) for note in snapshot.notes),
            tree=memory_tree(snapshot.notes),
            searches=tuple(_search_view(search) for search in snapshot.searches),
        )

    def document(
        self,
        session_id: SessionId,
        path: str | None,
        stem: str | None,
    ) -> DashboardMemoryDocument:
        document = self.memory.document(session_id, path, stem)
        if document.body is None:
            return DashboardMemoryDocument(
                document.name, "", (), "", (), True
            )
        return DashboardMemoryDocument(
            name=document.name,
            path=document.path,
            frontmatter=tuple(
                (html.escape(key), html.escape(value))
                for key, value in document.frontmatter
            ),
            html=_memory_html(document, self.memory, session_id),
            backlinks=document.backlinks,
            missing=False,
        )


def _search_view(search) -> dict:
    record = asdict(search)
    record["hits"] = tuple(
        dict(hit, viewable=bool(hit["path"]) and os.path.isfile(hit["path"]))
        for hit in record["hits"]
    )
    return record


def _node(path: str, name: str) -> dict:
    return {"name": name, "path": path, "directories": {}, "notes": []}


def memory_tree(notes: tuple[MemoryNoteRecord, ...]) -> dict:
    root = _node("", "")
    for note in notes:
        parts = [part for part in note.relative_path.split("/") if part]
        if not parts:
            parts = [note.name or "?"]
        current = root
        for segment in parts[:-1]:
            current = current["directories"].setdefault(
                segment,
                _node(
                    (current["path"] + "/" + segment).lstrip("/"),
                    segment,
                ),
            )
        record = asdict(note)
        record["label"] = parts[-1]
        current["notes"].append(record)
    _compress(root, top=True)
    _rollup(root)
    return root


def _compress(node: dict, *, top: bool = False) -> None:
    for child in list(node["directories"].values()):
        _compress(child)
    if top:
        return
    while len(node["directories"]) == 1 and not node["notes"]:
        child = next(iter(node["directories"].values()))
        node["name"] += "/" + child["name"]
        node["path"] = child["path"]
        node["notes"] = child["notes"]
        node["directories"] = child["directories"]
    if len(node["directories"]) == 1 and node["notes"]:
        child = next(iter(node["directories"].values()))
        if not child["directories"]:
            for note in child["notes"]:
                note["label"] = child["name"] + "/" + note["label"]
            node["notes"].extend(child["notes"])
            node["directories"] = {}


def _rollup(node: dict) -> None:
    note_count = len(node["notes"])
    write_count = sum(
        note["action"] in ("Write", "Update") for note in node["notes"]
    )
    for child in node["directories"].values():
        _rollup(child)
        note_count += child["note_count"]
        write_count += child["write_count"]
    node["note_count"] = note_count
    node["write_count"] = write_count
    node["directories"] = sorted(
        node["directories"].values(), key=lambda child: child["name"].lower()
    )
    node["notes"].sort(key=lambda note: note["label"].lower())


def _memory_html(
    document: MemoryDocument,
    memory: MemoryService,
    session_id: SessionId,
) -> str:
    links = []

    def protect(match: re.Match) -> str:
        links.append((match.group(1).strip(), (match.group(2) or "").strip()))
        return f"\x02{len(links) - 1}\x02"

    rendered = md_html(_LINK_PATTERN.sub(protect, document.body or ""))

    def restore(match: re.Match) -> str:
        stem, alias = links[int(match.group(1))]
        linked = memory.document(session_id, None, stem)
        dead = " dead" if linked.body is None else ""
        return (
            f'<a class="wl{dead}" data-note="{html.escape(stem, quote=True)}">'
            f"{html.escape(alias or stem)}</a>"
        )

    return _SENTINEL_PATTERN.sub(restore, rendered)

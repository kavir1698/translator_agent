"""Gate 2: deterministic style-anchor proposals and approval (plan 6.5.1)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from shahrzad.core.db import Database
from shahrzad.core.errors import ConfigError
from shahrzad.core.ids import sha256_text, utc_now_iso
from shahrzad.core.models import SegmentStatus

DRAFT_NAME = "anchors.draft.yaml"
APPROVED_NAME = "anchors.yaml"


@dataclass
class AnchorResult:
    path: Path | None = None
    approved: int = 0
    proposed: int = 0
    rejected: int = 0
    message: str = ""
    anchors: list[dict[str, Any]] = field(default_factory=list)


def _draft_path(base: Path) -> Path:
    return base / "artifacts" / DRAFT_NAME


def _approved_path(base: Path) -> Path:
    return base / "artifacts" / APPROVED_NAME


def load_anchor_draft(base: Path) -> dict[str, Any] | None:
    path = _draft_path(base)
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid anchors draft in {path}: {exc}") from exc
    return data if isinstance(data, dict) else None


def load_approved_anchors(base: Path) -> list[dict[str, Any]]:
    path = _approved_path(base)
    if not path.is_file():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid anchors file in {path}: {exc}") from exc
    if not isinstance(data, dict):
        return []
    anchors = data.get("anchors")
    if not isinstance(anchors, list):
        return []
    return [anchor for anchor in anchors if isinstance(anchor, dict)][:3]


def anchors_hash(anchors: list[dict[str, Any]]) -> str:
    payload = json.dumps(anchors, sort_keys=True, ensure_ascii=False)
    return f"sha256:{sha256_text(payload)}"


def _first_body_chapter(db: Database, book_id: int) -> Any:
    chapters = [chapter for chapter in db.chapters(book_id) if chapter["kind"] == "body"]
    if chapters:
        return chapters[0]
    chapters = db.chapters(book_id)
    return chapters[0] if chapters else None


def _emphasis_count(nodes_json: str) -> int:
    try:
        nodes = json.loads(nodes_json or "[]")
    except json.JSONDecodeError:
        return 0
    return sum(1 for node in nodes if isinstance(node, dict) and node.get("path"))


def propose_anchors(*, root: Path, slug: str | None, db: Database, book_id: int) -> AnchorResult:
    """Select up to three deterministic voice examples from the first body chapter."""
    _ = root, slug
    chapter = _first_body_chapter(db, book_id)
    if chapter is None:
        raise ConfigError("the book has no chapters", hint="re-run `shz init`")
    candidates: list[dict[str, Any]] = []
    for segment in db.segments(chapter_id=int(chapter["id"])):
        if segment["status"] != SegmentStatus.DONE:
            continue
        translation = db.current_translation(str(segment["id"]))
        if translation is None or not str(translation["fa_text"]).strip():
            continue
        candidates.append(
            {
                "segment_id": str(segment["id"]),
                "en": str(segment["source_text"]),
                "fa": str(translation["fa_text"]),
                "kind": str(segment["kind"]),
                "dialogue_ratio": float(segment["dialogue_ratio"]),
                "emphasis": _emphasis_count(str(segment["nodes_json"])),
                "length": len(str(segment["source_text"])),
            }
        )
    if not candidates:
        raise ConfigError(
            f"chapter {chapter['idx']} has no done segments to anchor",
            hint="run `shz translate --chapters 1` first",
        )

    picks: list[dict[str, Any]] = []
    used: set[str] = set()

    def pick(key: str, label: str, predicate: Any = None) -> None:
        pool = [item for item in candidates if item["segment_id"] not in used]
        if predicate is not None:
            pool = [item for item in pool if predicate(item)]
        if not pool:
            return
        chosen = sorted(pool, key=lambda item: (-item[key], item["segment_id"]))[0]
        used.add(chosen["segment_id"])
        picks.append({**chosen, "note": label})

    before = len(picks)
    pick(
        "length",
        "narration / longest prose",
        lambda item: item["kind"] == "prose" and item["dialogue_ratio"] < 0.05,
    )
    if len(picks) == before:
        pick("length", "narration / longest prose", lambda item: item["kind"] == "prose")
    pick("dialogue_ratio", "dialogue / longest exchange")
    pick("emphasis", "inline emphasis / most marked runs")
    picks = picks[:3]
    payload_candidates = [
        {
            "id": f"a{index:03d}",
            "segment_id": item["segment_id"],
            "en": item["en"],
            "fa": item["fa"],
            "note": item["note"],
            "status": "proposed",
        }
        for index, item in enumerate(picks, start=1)
    ]
    payload = {
        "version": 1,
        "chapter": int(chapter["idx"]),
        "generated_at": utc_now_iso(),
        "candidates": payload_candidates,
    }
    path = _draft_path(base=_root_for(db))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return AnchorResult(path=path, proposed=len(payload_candidates), anchors=payload_candidates)


def _root_for(db: Database) -> Path:
    return db.path.parent


def approve_anchors(
    *, db: Database, book_id: int, ids: list[str] | None = None
) -> AnchorResult:
    base = _root_for(db)
    draft = load_anchor_draft(base)
    if draft is None:
        raise ConfigError(
            "no anchors draft to approve",
            hint="run `shz anchors propose` (or `shz translate --calibrate`) first",
        )
    selected: list[dict[str, Any]] = []
    for candidate in draft.get("candidates") or []:
        if not isinstance(candidate, dict) or candidate.get("status") == "rejected":
            continue
        if ids and str(candidate.get("id")) not in ids:
            continue
        selected.append(
            {
                "id": str(candidate.get("id")),
                "en": str(candidate.get("en", "")),
                "fa": str(candidate.get("fa", "")),
                "note": str(candidate.get("note", "")),
            }
        )
    if len(selected) < 2:
        raise ConfigError(
            f"gate 2 needs at least 2 anchors, got {len(selected)}",
            hint="approve candidate ids from artifacts/anchors.draft.yaml",
        )
    selected = selected[:3]
    digest = anchors_hash(selected)
    payload = {
        "version": 1,
        "approved": utc_now_iso(),
        "hash": digest,
        "anchors": selected,
    }
    path = _approved_path(base)
    path.write_text(
        f"# approved: {payload['approved']}  hash: {digest}\n"
        + yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    db.set_book_metadata_value(book_id, "anchors_hash", digest)
    return AnchorResult(path=path, approved=len(selected), anchors=selected)


def reject_anchors(
    *, db: Database, book_id: int, ids: list[str]
) -> AnchorResult:
    _ = book_id
    base = _root_for(db)
    draft = load_anchor_draft(base)
    if draft is None:
        raise ConfigError("no anchors draft to reject")
    rejected = 0
    for candidate in draft.get("candidates") or []:
        if isinstance(candidate, dict) and str(candidate.get("id")) in ids:
            candidate["status"] = "rejected"
            rejected += 1
    path = _draft_path(base)
    path.write_text(
        yaml.safe_dump(draft, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return AnchorResult(path=path, rejected=rejected)


def list_anchors(*, db: Database) -> dict[str, list[dict[str, Any]]]:
    base = _root_for(db)
    approved = load_approved_anchors(base)
    draft = load_anchor_draft(base)
    return {
        "approved": approved,
        "draft": list((draft or {}).get("candidates") or []),
    }

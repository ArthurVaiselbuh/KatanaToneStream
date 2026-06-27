"""Local patch cache — stores downloaded .tsl files, art, and an index."""

import contextlib
import json
from datetime import UTC, datetime
from pathlib import Path

from . import paths
from .models import PatchMeta


def _meta_to_dict(meta: PatchMeta) -> dict:
    return {
        "id": meta.id,
        "name": meta.name,
        "author": meta.author,
        "source": meta.source,
        "rating": meta.rating,
        "download_url": meta.download_url,
        "cached": True,
        "last_used": meta.last_used.isoformat() if meta.last_used else None,
        "image_url": meta.image_url,
    }


def _dict_to_meta(d: dict) -> PatchMeta:
    last_used = None
    if d.get("last_used"):
        with contextlib.suppress(ValueError):
            last_used = datetime.fromisoformat(d["last_used"])
    return PatchMeta(
        id=d["id"],
        name=d["name"],
        author=d.get("author", ""),
        source=d.get("source", "local"),
        rating=float(d.get("rating", 0.0)),
        download_url=d.get("download_url", ""),
        cached=True,
        last_used=last_used,
        image_url=d.get("image_url", ""),
    )


def load_index() -> dict[str, PatchMeta]:
    index_file = paths.index_file()
    if not index_file.exists():
        return {}
    try:
        with open(index_file, encoding="utf-8") as f:
            raw: dict = json.load(f)
        return {k: _dict_to_meta(v) for k, v in raw.items()}
    except (json.JSONDecodeError, KeyError):
        return {}


def _save_index(index: dict[str, PatchMeta]) -> None:
    paths.ensure_dirs()
    with open(paths.index_file(), "w", encoding="utf-8") as f:
        json.dump({k: _meta_to_dict(v) for k, v in index.items()}, f, indent=2)


# ── Patch files ───────────────────────────────────────────────────────────────


def save_patch(meta: PatchMeta, raw_bytes: bytes) -> None:
    paths.ensure_dirs()
    (paths.cache_dir() / f"{meta.id}.tsl").write_bytes(raw_bytes)
    index = load_index()
    meta.cached = True
    index[meta.id] = meta
    _save_index(index)


def get_patch_bytes(patch_id: str) -> bytes | None:
    path = paths.cache_dir() / f"{patch_id}.tsl"
    return path.read_bytes() if path.exists() else None


def is_cached(patch_id: str) -> bool:
    return (paths.cache_dir() / f"{patch_id}.tsl").exists()


def mark_used(patch_id: str) -> None:
    index = load_index()
    if patch_id in index:
        index[patch_id].last_used = datetime.now(UTC)
        _save_index(index)


def get_cached_patches(query: str = "") -> list[PatchMeta]:
    index = load_index()
    results = list(index.values())
    if query:
        q = query.lower()
        results = [m for m in results if q in m.name.lower() or q in m.author.lower()]
    results.sort(
        key=lambda m: m.last_used or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )
    return results


# ── Album art ─────────────────────────────────────────────────────────────────


def _art_path(patch_id: str) -> Path:
    return paths.art_dir() / f"{patch_id}.art"


def get_art_path(patch_id: str) -> Path | None:
    p = _art_path(patch_id)
    return p if p.exists() else None


def save_art(patch_id: str, data: bytes) -> None:
    paths.ensure_dirs()
    _art_path(patch_id).write_bytes(data)


# ── Delete ────────────────────────────────────────────────────────────────────


def delete_patch(patch_id: str) -> None:
    """Remove a patch and its art from the local cache and index."""
    tsl = paths.cache_dir() / f"{patch_id}.tsl"
    if tsl.exists():
        tsl.unlink()
    art = _art_path(patch_id)
    if art.exists():
        art.unlink()
    index = load_index()
    if patch_id in index:
        del index[patch_id]
        _save_index(index)

"""PatchService — search/download/parse/apply business logic, free of any UI.

The GUI delegates to this so the core flow can be unit-tested without Flet and
without a real amp connected (inject fakes for ``fetcher``/``midi`` in tests).
"""

import logging
from collections.abc import Callable

from . import cache
from .fetcher import download_patch, scrape_guitarpatches, scrape_toneexchange
from .katana_midi import KatanaMidi
from .models import KatanaPatch, PatchMeta
from .parser import parse_alb, parse_tsl

log = logging.getLogger(__name__)

StatusCallback = Callable[[str], None]


class PatchService:
    """Owns the MIDI connection and orchestrates the search → apply flow."""

    def __init__(self, midi: KatanaMidi | None = None) -> None:
        self.midi = midi or KatanaMidi()

    # ── Search ──────────────────────────────────────────────────────────────
    def search(self, query: str, source_filter: str) -> list[PatchMeta]:
        """Merge results from the selected sources, de-duplicated by id.

        Local cached patches always lead (when there's a query) so a previously
        used tone surfaces first regardless of the active source filter.
        """
        results: list[PatchMeta] = []
        seen: set[str] = set()

        def add(metas: list[PatchMeta]) -> None:
            for m in metas:
                if m.id not in seen:
                    seen.add(m.id)
                    results.append(m)

        if query:
            add(cache.get_cached_patches(query))
        if source_filter in ("all", "toneexchange"):
            add(scrape_toneexchange(query))
        if source_filter in ("all", "guitarpatches"):
            add(scrape_guitarpatches(query))
        if source_filter == "cached":
            add(cache.get_cached_patches(query))

        return results

    def load_cached(self, query: str = "") -> list[PatchMeta]:
        return cache.get_cached_patches(query)

    # ── Apply ───────────────────────────────────────────────────────────────
    def apply(
        self,
        meta: PatchMeta,
        target_patch: int | None,
        on_status: StatusCallback | None = None,
        on_cached: Callable[[], None] | None = None,
    ) -> KatanaPatch:
        """Download (cache-aware), parse, and send a patch to the amp.

        ``on_status`` receives human-readable progress strings; ``on_cached`` is
        fired once if a fresh download was saved to the cache. Raises on failure;
        callers handle/report the exception.
        """
        def status(msg: str) -> None:
            if on_status:
                on_status(msg)

        status(f"Downloading '{meta.name}'…")
        raw = cache.get_patch_bytes(meta.id) if cache.is_cached(meta.id) else None
        if not raw:
            raw = download_patch(meta)
            cache.save_patch(meta, raw)
            if on_cached:
                on_cached()

        status(f"Parsing '{meta.name}'…")
        patch = self._parse(raw)
        if patch is None:
            raise ValueError(f"No patches found in '{meta.name}'")
        patch.meta = meta

        slot = _slot_name(target_patch)
        status(f"Sending '{patch.display_name}' → {slot}…")
        if not self.midi.is_connected():
            self.midi.connect()
        self.midi.send_patch(patch, target_patch=target_patch)
        cache.mark_used(meta.id)
        status(f"Applied '{patch.display_name}' → {slot} ✓")
        return patch

    @staticmethod
    def _parse(raw: bytes) -> KatanaPatch | None:
        patches = parse_tsl(raw) or parse_alb(raw)  # .alb fallback for cached files
        return patches[0] if patches else None


_SLOT_NAMES = [f"{b}{n}" for b in "ABCDE" for n in range(1, 9)]  # A1…E8


def _slot_name(target_patch: int | None) -> str:
    return _SLOT_NAMES[target_patch] if target_patch is not None else "TONE"

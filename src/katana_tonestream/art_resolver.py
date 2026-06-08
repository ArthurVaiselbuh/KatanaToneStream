"""Art resolver — looks up album art via iTunes Search API using patch metadata.

The source sites (ToneExchange, guitarpatches.com) often provide no image or just
a photo of the amp. This module derives a search query from the patch name and
fetches real album art from iTunes, which needs no API key.

Entry point: ``resolve_art(meta)`` — returns image bytes or None.
Uses the same cache module as the rest of the app; call ``cache.save_art`` on the
result and it won't be fetched again.
"""

import logging
import re

import requests

from .models import PatchMeta

log = logging.getLogger(__name__)

_ITUNES_SEARCH = "https://itunes.apple.com/search"
_TIMEOUT = 8

# Words that describe a guitar patch but obscure the song title.
# Removed as whole words (word-boundary match, case-insensitive).
_NOISE = re.compile(
    r"\b("
    r"tone|tones|patch|patches|"
    r"intro|intr|verse|chorus|bridge|outro|"
    r"solo|lead|rhythm|ryhthm|riff|lick|"
    r"clean|crunch|dist|distortion|"
    r"overdrive|od|boost|fuzz|"
    r"v\d+|pt\.?\s*\d+|part\.?\s*\d+"
    r")\b",
    re.IGNORECASE,
)

# Separators like " - " or " | " that typically split "Artist - Song" in patch names.
_SEPARATOR = re.compile(r"\s*[-|/]\s*")


def _candidates(meta: PatchMeta) -> list[str]:
    """Return search query candidates in preference order.

    Strategy:
    - If name contains a separator (e.g. "Slash - November Rain Tone"), try both
      halves as artist+song (more specific) and the full cleaned name.
    - Otherwise just clean the name.
    """
    name = meta.name.strip()
    queries: list[str] = []

    parts = _SEPARATOR.split(name, maxsplit=1)
    if len(parts) == 2:
        # "Slash - November Rain Tone" → "Slash November Rain"
        combined = " ".join(_clean(p) for p in parts if _clean(p))
        if combined:
            queries.append(combined)

    cleaned = _clean(name)
    if cleaned and cleaned not in queries:
        queries.append(cleaned)

    return queries


def _clean(text: str) -> str:
    text = _NOISE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip(" -|/")
    return text


def _artwork_url(url100: str) -> str:
    """Upgrade the iTunes thumbnail URL to 600×600."""
    return re.sub(r"\d+x\d+bb", "600x600bb", url100)


def _itunes_search(query: str) -> bytes | None:
    """Search iTunes and return the first result's album art bytes, or None."""
    try:
        resp = requests.get(
            _ITUNES_SEARCH,
            params={"term": query, "media": "music", "entity": "song", "limit": 3},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except (requests.RequestException, ValueError) as exc:
        log.debug("iTunes search failed for %r: %s", query, exc)
        return None

    if not results:
        log.debug("iTunes: no results for %r", query)
        return None

    art_url = results[0].get("artworkUrl100", "")
    if not art_url:
        return None
    art_url = _artwork_url(art_url)

    artist = results[0].get("artistName", "")
    track = results[0].get("trackName", "")
    log.debug("iTunes art: %r → %r / %r", query, artist, track)

    try:
        art_resp = requests.get(art_url, timeout=_TIMEOUT)
        art_resp.raise_for_status()
        return art_resp.content
    except requests.RequestException as exc:
        log.debug("iTunes art download failed: %s", exc)
        return None


def resolve_art(meta: PatchMeta) -> bytes | None:
    """Return album art bytes for *meta* by searching iTunes, or None if not found."""
    for query in _candidates(meta):
        data = _itunes_search(query)
        if data:
            return data
    log.debug("Art resolver: no art found for %r", meta.name)
    return None

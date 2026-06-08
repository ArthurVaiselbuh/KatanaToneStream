"""Web scrapers for Boss tone patch sites."""

import hashlib
import logging
import re

import requests
from bs4 import BeautifulSoup

from .config import toneexchange_credentials
from .models import PatchMeta

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
_TIMEOUT = 15


def _stable_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Boss Tone Exchange  (Roland API — no auth required for search)
# ---------------------------------------------------------------------------
_BTE_API = "https://rcpsvc.roland.com/btc"
_BTE_SEARCH = f"{_BTE_API}/searchLivesets/"
# Katana Mk2 and Gen3 gear slugs on ToneExchange
_BTE_GEARS = ["katana_mk2", "katana_gen_3"]


def scrape_toneexchange(query: str = "") -> list[PatchMeta]:
    """Search Boss Tone Exchange for Katana patches via the Roland API."""
    results: list[PatchMeta] = []
    seen: set[str] = set()

    api_headers = {**_HEADERS, "Accept": "application/json",
                   "Origin": "https://bosstoneexchange.com",
                   "Referer": "https://bosstoneexchange.com/"}

    for gear in _BTE_GEARS:
        params: dict[str, str] = {"gear": gear}
        if query:
            params["keyword"] = query

        try:
            resp = requests.get(_BTE_SEARCH, params=params, headers=api_headers, timeout=_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            log.warning("ToneExchange API error for gear=%s: %s", gear, exc)
            continue

        for item in data.get("items", []):
            liveset_id = item.get("livesetId", "")
            if not liveset_id or liveset_id in seen:
                continue
            seen.add(liveset_id)

            download_url = f"{_BTE_API}/livesets/{liveset_id}/download"
            name = item.get("name", "").strip() or "Unnamed"

            results.append(
                PatchMeta(
                    id=_stable_id(liveset_id),
                    name=name,
                    author=item.get("creatorName", "").strip(),
                    source="toneexchange",
                    rating=0.0,
                    download_url=download_url,
                    image_url=item.get("imageUrl", ""),
                )
            )

    log.info("ToneExchange: found %d patches for query=%r", len(results), query)
    return results


# ---------------------------------------------------------------------------
# GuitarPatches.com
# ---------------------------------------------------------------------------
_GP_BASE = "https://guitarpatches.com"
_GP_PATCHES = f"{_GP_BASE}/patches.php"
_GP_SEARCH = f"{_GP_BASE}/search.php"
_GP_DOWNLOAD = f"{_GP_BASE}/download.php"
# Unit names as they appear on guitarpatches.com
_GP_UNITS = ["KATANAMKII", "Katana"]


def scrape_guitarpatches(query: str = "") -> list[PatchMeta]:
    """Search guitarpatches.com for Katana patches."""
    results: list[PatchMeta] = []
    seen: set[str] = set()

    for unit in _GP_UNITS:
        try:
            if query:
                resp = requests.post(
                    f"{_GP_SEARCH}?unit={unit}",
                    data={"sstring": query},
                    headers=_HEADERS,
                    timeout=_TIMEOUT,
                )
            else:
                resp = requests.get(
                    _GP_PATCHES, params={"unit": unit}, headers=_HEADERS, timeout=_TIMEOUT
                )
            resp.raise_for_status()
        except requests.RequestException as exc:
            log.warning("GuitarPatches request failed for unit=%s: %s", unit, exc)
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.find_all(attrs={"onclick": re.compile(r"mode=show")})

        for card in cards:
            m = re.search(r"ID=(\d+)", card.get("onclick", ""))
            if not m:
                continue
            patch_id = m.group(1)
            uid = f"gp_{unit}_{patch_id}"
            if uid in seen:
                continue
            seen.add(uid)

            h1 = card.find("h1")
            h2 = card.find("h2")
            name = h1.get_text(strip=True) if h1 else f"Patch {patch_id}"
            artist = h2.get_text(strip=True) if h2 else ""

            download_url = f"{_GP_DOWNLOAD}?unit={unit}&mode=download&ID={patch_id}"

            results.append(
                PatchMeta(
                    id=_stable_id(download_url),
                    name=name,
                    author=artist,
                    source="guitarpatches",
                    rating=0.0,
                    download_url=download_url,
                )
            )

    log.info("GuitarPatches: found %d patches for query=%r", len(results), query)
    return results


# ---------------------------------------------------------------------------
# Boss Tone Exchange auth
# ---------------------------------------------------------------------------
_BTE_SIGNIN = f"{_BTE_API}/signin"
_bte_id_token: str | None = None


def _bte_login() -> str:
    """Login to Boss Tone Exchange and return an idToken. Caches the token for the session."""
    global _bte_id_token
    if _bte_id_token:
        return _bte_id_token

    username, password = toneexchange_credentials()
    if not username or not password:
        raise RuntimeError(
            "Boss Tone Exchange credentials not configured. "
            "Add your username and password to config.ini under [toneexchange]."
        )

    api_headers = {
        **_HEADERS,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://bosstoneexchange.com",
        "Referer": "https://bosstoneexchange.com/",
    }
    resp = requests.post(
        _BTE_SIGNIN,
        json={"email": username, "password": password},
        headers=api_headers,
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    _bte_id_token = resp.json()["idToken"]
    log.info("Signed in to Boss Tone Exchange as %s", username)
    return _bte_id_token


def _bte_download(download_api_url: str) -> bytes:
    """Authenticate with ToneExchange, resolve the pre-signed download URL, fetch bytes."""
    api_headers = {
        **_HEADERS,
        "Accept": "application/json",
        "Authorization": _bte_login(),
        "Origin": "https://bosstoneexchange.com",
        "Referer": "https://bosstoneexchange.com/",
    }

    resp = requests.get(download_api_url, headers=api_headers, timeout=_TIMEOUT)

    # Token expired — clear cache, retry once
    if resp.status_code in (401, 403):
        global _bte_id_token
        _bte_id_token = None
        api_headers["Authorization"] = _bte_login()
        resp = requests.get(download_api_url, headers=api_headers, timeout=_TIMEOUT)

    resp.raise_for_status()
    file_url = resp.json()["downloadUrl"]
    file_resp = requests.get(file_url, headers=_HEADERS, timeout=30)
    file_resp.raise_for_status()
    return file_resp.content


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------
def fetch_art(meta: PatchMeta) -> bytes | None:
    """Download album art for a patch. Returns None if unavailable."""
    if not meta.image_url:
        return None
    try:
        resp = requests.get(meta.image_url, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        return resp.content
    except requests.RequestException as exc:
        log.debug("Art fetch failed for %s: %s", meta.id, exc)
        return None


def download_patch(meta: PatchMeta) -> bytes:
    """Download the raw patch file bytes for a PatchMeta."""
    if meta.source == "toneexchange":
        return _bte_download(meta.download_url)
    resp = requests.get(meta.download_url, headers=_HEADERS, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.content

"""Tests for the local patch cache (against a temp app dir)."""

from katana_tonestream import cache
from katana_tonestream.models import PatchMeta


def _meta(patch_id="p1", name="Hotel Solo", author="Eagles") -> PatchMeta:
    return PatchMeta(
        id=patch_id, name=name, author=author,
        source="toneexchange", rating=4.5, download_url="http://x",
    )


def test_save_and_load_roundtrip(app_home):
    meta = _meta()
    cache.save_patch(meta, b"raw-tsl-bytes")

    assert cache.is_cached("p1")
    assert cache.get_patch_bytes("p1") == b"raw-tsl-bytes"

    index = cache.load_index()
    assert "p1" in index
    assert index["p1"].name == "Hotel Solo"
    assert index["p1"].cached is True


def test_get_cached_patches_filters_by_query(app_home):
    cache.save_patch(_meta("p1", "Hotel California", "Eagles"), b"a")
    cache.save_patch(_meta("p2", "Money for Nothing", "Dire Straits"), b"b")

    by_name = cache.get_cached_patches("hotel")
    assert [m.id for m in by_name] == ["p1"]

    by_author = cache.get_cached_patches("dire")
    assert [m.id for m in by_author] == ["p2"]

    assert len(cache.get_cached_patches()) == 2


def test_missing_patch_returns_none(app_home):
    assert cache.get_patch_bytes("nope") is None
    assert cache.is_cached("nope") is False


def test_empty_index_when_nothing_saved(app_home):
    assert cache.load_index() == {}

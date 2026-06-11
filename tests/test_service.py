"""Tests for PatchService.apply with fake fetcher + fake MIDI."""

import json

import pytest

from katana_tonestream import service as service_module
from katana_tonestream.models import PatchMeta
from katana_tonestream.service import PatchService


class FakeMidi:
    def __init__(self, connected=True):
        self.connected = connected
        self.sent = None
        self.connect_calls = 0

    def is_connected(self):
        return self.connected

    def connect(self):
        self.connect_calls += 1
        self.connected = True
        return True

    def send_patch(self, patch, target_patch=None):
        self.sent = (patch, target_patch)


def _meta(patch_id="t1", name="Test Patch"):
    return PatchMeta(
        id=patch_id,
        name=name,
        author="",
        source="local",
        rating=0.0,
        download_url="http://x",
    )


def _alb_bytes(name_hex):
    return json.dumps({"userPatch": [{"UserPatch%PatchName": name_hex}]}).encode()


def test_apply_happy_path(app_home, monkeypatch, sample_tsl_bytes):
    monkeypatch.setattr(service_module, "download_patch", lambda meta: sample_tsl_bytes)
    midi = FakeMidi()
    svc = PatchService(midi=midi)

    statuses = []
    patch = svc.apply(_meta(), target_patch=None, on_status=statuses.append)

    assert midi.sent is not None
    sent_patch, sent_target = midi.sent
    assert sent_patch is patch
    assert sent_target is None
    # status sequence covers the whole flow
    joined = " | ".join(statuses)
    assert "Downloading" in joined
    assert "Parsing" in joined
    assert "Sending" in joined
    assert "Applied" in joined


def test_apply_uses_cache_and_skips_download(app_home, monkeypatch):
    from katana_tonestream import cache

    meta = _meta()
    cache.save_patch(meta, _alb_bytes(["0x48", "0x69"]))  # pre-cached "Hi"

    def _boom(meta):
        raise AssertionError("download_patch should not be called for cached patch")

    monkeypatch.setattr(service_module, "download_patch", _boom)
    midi = FakeMidi()
    svc = PatchService(midi=midi)

    patch = svc.apply(meta, target_patch=5)
    assert midi.sent == (patch, 5)


def test_apply_parses_alb_fallback(app_home, monkeypatch):
    monkeypatch.setattr(service_module, "download_patch", lambda meta: _alb_bytes(["0x41"]))
    midi = FakeMidi()
    svc = PatchService(midi=midi)

    on_cached = []
    patch = svc.apply(_meta(), target_patch=None, on_cached=lambda: on_cached.append(True))

    assert patch.raw_bytes is not None  # came from the .alb branch
    assert on_cached == [True]  # fired because it was freshly downloaded


def test_apply_connects_when_disconnected(app_home, monkeypatch, sample_tsl_bytes):
    monkeypatch.setattr(service_module, "download_patch", lambda meta: sample_tsl_bytes)
    midi = FakeMidi(connected=False)
    svc = PatchService(midi=midi)

    svc.apply(_meta(), target_patch=None)
    assert midi.connect_calls == 1


def test_apply_raises_when_no_patches(app_home, monkeypatch):
    monkeypatch.setattr(service_module, "download_patch", lambda meta: b"{}")
    svc = PatchService(midi=FakeMidi())

    with pytest.raises(ValueError):
        svc.apply(_meta(), target_patch=None)

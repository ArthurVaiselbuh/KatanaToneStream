"""Shared pytest fixtures."""

import glob

import pytest

from katana_tonestream import config


@pytest.fixture
def app_home(tmp_path, monkeypatch):
    """Point all app directories at a fresh temp dir and reset cached config."""
    monkeypatch.setenv("KATANA_TONESTREAM_HOME", str(tmp_path))
    config.reload()
    yield tmp_path
    config.reload()


@pytest.fixture(scope="session")
def sample_tsl_path():
    """A real Boss Tone Studio .tsl file shipped under prerequisites/."""
    matches = glob.glob("prerequisites/**/*.tsl", recursive=True)
    if not matches:
        pytest.skip("no sample .tsl files available")
    return matches[0]


@pytest.fixture(scope="session")
def sample_tsl_bytes(sample_tsl_path):
    with open(sample_tsl_path, "rb") as f:
        return f.read()

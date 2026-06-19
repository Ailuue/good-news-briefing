"""The SQLite seen-store -- the memory that keeps a story from showing up twice.

tmp_path gives each test a throwaway directory, so we point the store's DB there
and exercise the real sqlite code without touching your ~/good-news data.
"""

from __future__ import annotations

import pytest

from good_news import config
from good_news.store import SeenStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUT_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "seen.sqlite3")
    return SeenStore.open()


def test_unseen_link_is_not_seen(store):
    assert store.is_seen("https://example.com/new") is False


def test_marked_link_is_seen(store):
    store.mark_seen("https://example.com/a")
    assert store.is_seen("https://example.com/a") is True
    assert store.is_seen("https://example.com/b") is False


def test_mark_is_idempotent(store):
    # INSERT OR IGNORE: marking twice must not raise on the PRIMARY KEY.
    store.mark_seen("https://example.com/a")
    store.mark_seen("https://example.com/a")
    assert store.is_seen("https://example.com/a") is True


def test_seen_persists_across_reopen(store, tmp_path, monkeypatch):
    store.mark_seen("https://example.com/a")
    store.commit()
    # A fresh store over the same DB file should still remember the link.
    reopened = SeenStore.open()
    assert reopened.is_seen("https://example.com/a") is True

"""RSS -> Article parsing, plus the best-effort article crawl. The network is
mocked: reddit_external_link works on already-parsed entries, and
fetch_article_text gets a fake httpx.get so no real HTTP happens.
"""

from __future__ import annotations

from good_news import sources


# --- reddit_external_link: dig the real URL out of reddit's RSS HTML --------

def test_reddit_link_extracts_external_url():
    entry = {
        "content": [
            {"value": '<a href="https://news.example.com/story">[link]</a> '
                      '<a href="https://reddit.com/r/x/comments/1">[comments]</a>'}
        ]
    }
    assert sources.reddit_external_link(entry) == "https://news.example.com/story"


def test_reddit_link_ignores_self_posts():
    # Self-posts point [link] back at reddit -- not an external article.
    entry = {"summary": '<a href="https://reddit.com/r/x/1">[link]</a>'}
    assert sources.reddit_external_link(entry) == ""


def test_reddit_link_unescapes_ampersands():
    entry = {"summary": '<a href="https://ex.com/a?b=1&amp;c=2">[link]</a>'}
    assert sources.reddit_external_link(entry) == "https://ex.com/a?b=1&c=2"


def test_reddit_link_returns_empty_when_absent():
    assert sources.reddit_external_link({"summary": "no anchors here"}) == ""


# --- _TextExtractor: strip HTML down to readable text ----------------------

def test_text_extractor_skips_script_and_style():
    p = sources._TextExtractor()
    p.feed("<p>Keep this</p><script>var x = 1;</script><style>.a{}</style>")
    assert "Keep this" in p.parts
    assert "var x = 1;" not in p.parts


# --- fetch_article_text: crawl with the network mocked ---------------------

class _FakeResp:
    def __init__(self, text, content_type="text/html"):
        self.text = text
        self.headers = {"content-type": content_type}

    def raise_for_status(self):
        pass


def test_fetch_article_text_extracts_body(monkeypatch):
    import httpx

    html = "<html><body><p>Trees were planted.</p></body></html>"
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResp(html))
    assert sources.fetch_article_text("https://example.com") == "Trees were planted."


def test_fetch_article_text_skips_non_html(monkeypatch):
    import httpx

    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: _FakeResp("%PDF...", "application/pdf")
    )
    assert sources.fetch_article_text("https://example.com/x.pdf") == ""


def test_fetch_article_text_returns_empty_on_error(monkeypatch):
    import httpx

    def boom(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", boom)
    # Any failure must fall back to "" so the caller can use the RSS summary.
    assert sources.fetch_article_text("https://example.com") == ""

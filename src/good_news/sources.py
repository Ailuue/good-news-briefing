"""Turning RSS feeds into Articles.

fetch() pulls every feed in config.FEEDS and returns a flat list of Articles.
For reddit link-posts it points the link at the submitted article (not the
comments page) and flags it so the pipeline can crawl the real body; the crawl
itself lives here too (fetch_article_text) since it's a source concern.
"""

from __future__ import annotations
import re
import sys
from html.parser import HTMLParser
from typing import Any
import feedparser

from . import config
from .models import Article

# Reddit RSS entries carry their submission as HTML containing a "[link]"
# anchor (the submitted article) and a "[comments]" anchor (the thread).
_REDDIT_LINK_RE = re.compile(r'href="([^"]+)"[^>]*>\s*\[link\]', re.IGNORECASE)


def reddit_external_link(entry: Any) -> str:
    """Return the real article URL a reddit post links to, or '' if there is
    none. entry.link points at the comments page; the submitted URL lives in
    the [link] anchor of the entry's HTML. Self-posts point [link] back at
    reddit, so those (and non-reddit entries) return ''."""
    html = ""
    contents = entry.get("content") or []
    if contents:
        html = contents[0].get("value", "") or ""
    html = html or entry.get("summary", "") or ""
    m = _REDDIT_LINK_RE.search(html)
    if not m:
        return ""
    url = m.group(1).replace("&amp;", "&")
    return "" if "reddit.com" in url else url


class _TextExtractor(HTMLParser):
    """Collect human-readable text, skipping non-content tags. Good enough to
    give the classifier the gist of an article without pulling in a readability
    dependency."""

    _SKIP = {"script", "style", "noscript", "head", "nav", "footer", "header",
             "aside", "form", "svg"}

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            text = data.strip()
            if text:
                self.parts.append(text)


def fetch_article_text(url: str, max_chars: int = config.ARTICLE_MAX_CHARS) -> str:
    """Best-effort crawl of an article's readable text. Returns '' on any
    failure so the caller can fall back to the RSS summary. Uses httpx, which
    ships with the openai dependency (no extra install)."""
    try:
        import httpx

        r = httpx.get(
            url,
            follow_redirects=True,
            timeout=config.ARTICLE_FETCH_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0 (good-news-briefing)"},
        )
        r.raise_for_status()
        if "html" not in r.headers.get("content-type", "").lower():
            return ""  # PDFs, images, etc. -- nothing to extract
        parser = _TextExtractor()
        parser.feed(r.text)
        text = " ".join(parser.parts)
        return re.sub(r"\s+", " ", text).strip()[:max_chars]
    except Exception as e:
        print(f"  ! article fetch failed {url}: {e}", file=sys.stderr)
        return ""


def fetch(per_feed: int = config.MAX_ENTRIES_PER_FEED) -> list[Article]:
    out: list[Article] = []
    for url in config.FEEDS:
        try:
            d: Any = feedparser.parse(url)
        except Exception as e:
            print(f"  ! feed failed {url}: {e}", file=sys.stderr)
            continue
        # feedparser swallows network/SSL/parse errors into d.bozo rather than
        # raising, so warn (instead of silently fetching 0) when a feed breaks.
        if d.bozo and not d.entries:
            print(f"  ! feed error {url}: {d.bozo_exception!r}", file=sys.stderr)
            continue
        src = d.feed.get("title", url)
        for e in d.entries[:per_feed]:
            link = e.get("link", "")
            # For reddit link-posts, point at the article instead of the
            # comments page and remember to crawl it. Self-posts have no
            # external link, so leave them as-is.
            is_reddit_article = False
            if "reddit.com" in link:
                ext = reddit_external_link(e)
                if ext:
                    link = ext
                    is_reddit_article = True
            out.append(
                Article(
                    title=(e.get("title") or "").strip(),
                    summary=(e.get("summary") or e.get("description") or "")[:1200],
                    link=link,
                    source=src,
                    is_reddit_article=is_reddit_article,
                )
            )
    return out

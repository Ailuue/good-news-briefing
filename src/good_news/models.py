"""Typed data structures that flow through the pipeline.

Article carries a story from fetch -> classify -> digest; the verdict-derived
fields (category/optimism/reason) are filled in once it clears the filter.
Verdict is the model's structured judgement of a single article.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any


@dataclass
class Article:
    title: str
    summary: str
    link: str
    source: str
    is_reddit_article: bool = False
    # Filled in after a kept verdict (see pipeline.run).
    category: str | None = None
    optimism: float | None = None
    reason: str | None = None


@dataclass
class Verdict:
    is_good_news: bool
    category: str
    optimism: float
    is_corporate_pr: bool
    is_pure_luck: bool
    reason: str

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Verdict":
        """Build a Verdict from the model's JSON, tolerating missing keys."""
        return cls(
            is_good_news=bool(data.get("is_good_news")),
            category=str(data.get("category", "other")),
            optimism=float(data.get("optimism", 0)),
            is_corporate_pr=bool(data.get("is_corporate_pr")),
            is_pure_luck=bool(data.get("is_pure_luck")),
            reason=str(data.get("reason", "")),
        )

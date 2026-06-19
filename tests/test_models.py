"""Verdict.from_json has to tolerate whatever the model returns -- missing keys,
wrong types, partial JSON -- without crashing the run. These tests pin that
defensive behaviour.
"""

from __future__ import annotations

from good_news.models import Verdict


def test_from_json_full():
    v = Verdict.from_json(
        {
            "is_good_news": True,
            "category": "health",
            "optimism": 0.8,
            "is_corporate_pr": False,
            "is_pure_luck": False,
            "reason": "a real advance",
        }
    )
    assert v.is_good_news and v.category == "health" and v.optimism == 0.8


def test_from_json_empty_uses_defaults():
    v = Verdict.from_json({})
    assert v.is_good_news is False
    assert v.category == "other"
    assert v.optimism == 0.0
    assert v.reason == ""


def test_from_json_coerces_types():
    # Models sometimes emit optimism as a string or 0/1 ints for booleans.
    v = Verdict.from_json({"optimism": "0.75", "is_good_news": 1})
    assert v.optimism == 0.75
    assert v.is_good_news is True

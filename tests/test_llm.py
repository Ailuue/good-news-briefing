"""Testing the model-calling functions WITHOUT a model.

This is the template for the whole "AI pipeline" testing approach: we replace
`llm.client` (the OpenAI client) with a fake whose `.create` returns a canned,
OpenAI-shaped response. Then classify()/write_digest() run for real against
known input -- so we test OUR parsing, guardrails, and error handling, never the
model's judgement (that's an eval, not a unit test).
"""

from __future__ import annotations
import json
from types import SimpleNamespace

import pytest

from good_news import llm
from conftest import fake_chat


def install_fake_client(monkeypatch, response):
    """Point llm.client at a fake that returns `response` from both the chat
    and embeddings endpoints. Returns a list that records the call kwargs."""
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return response

    fake = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
        embeddings=SimpleNamespace(create=create),
    )
    monkeypatch.setattr(llm, "client", fake)
    return calls


# --- classify(): RSS item -> structured Verdict ----------------------------

def test_classify_parses_verdict(monkeypatch, article):
    payload = {
        "is_good_news": True,
        "category": "environment",
        "optimism": 0.8,
        "is_corporate_pr": False,
        "is_pure_luck": False,
        "reason": "real reforestation",
    }
    install_fake_client(monkeypatch, fake_chat(content=json.dumps(payload)))

    v = llm.classify(article)
    assert v is not None
    assert v.category == "environment" and v.optimism == 0.8


def test_classify_returns_none_on_bad_json(monkeypatch, article):
    # Model emitted prose instead of JSON -- classify must swallow it and skip
    # the article rather than crash the whole run.
    install_fake_client(monkeypatch, fake_chat(content="Sorry, I can't do that."))
    assert llm.classify(article) is None


def test_classify_returns_none_when_client_raises(monkeypatch, article):
    def boom(**kwargs):
        raise ConnectionError("LM Studio offline")

    fake = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=boom))
    )
    monkeypatch.setattr(llm, "client", fake)
    assert llm.classify(article) is None


# --- write_digest(): items -> markdown, with link splicing -----------------

def test_write_digest_restores_links(monkeypatch, article):
    article.category, article.reason = "environment", "great stuff"
    # The "model" echoes the @@1@@ marker; restore_links swaps in the real URL.
    install_fake_client(
        monkeypatch, fake_chat(content="Wonderful news! @@1@@")
    )

    out = llm.write_digest([article])
    assert article.link in out
    assert "@@1@@" not in out


def test_write_digest_raises_on_truncation(monkeypatch, article):
    # finish_reason == "length" is how chain-of-thought leaked before; fail loud.
    install_fake_client(
        monkeypatch, fake_chat(content="half a dige", finish_reason="length")
    )
    with pytest.raises(RuntimeError, match="token cap"):
        llm.write_digest([article])


def test_write_digest_raises_on_empty_answer(monkeypatch, article):
    # Model emitted only reasoning -> answer_text is blank -> no digest.
    install_fake_client(monkeypatch, fake_chat(content="<think>hmm</think>"))
    with pytest.raises(RuntimeError, match="no digest text"):
        llm.write_digest([article])

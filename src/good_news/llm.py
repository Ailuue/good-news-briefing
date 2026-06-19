"""The LM Studio / OpenAI-client wrapper: classify(), write_digest(), embed().

LM Studio speaks the OpenAI API, so we talk to it through the openai client
pointed at the LAN host. The model is local, so the api_key can be anything.
"""

from __future__ import annotations
import json
import sys
from typing import Any, cast
from openai import OpenAI

from . import config
from .models import Article, Verdict
from .prompts import CRITERIA, DIGEST_PROMPT, VERDICT_SCHEMA

client = OpenAI(base_url=config.BASE_URL, api_key="lm-studio")  # key can be anything


def think_suffix() -> str:
    """Qwen3: '/no_think' at the end of a prompt disables reasoning tokens."""
    return "" if config.THINKING else " /no_think"


def message_text(msg: Any) -> str:
    """Pull the answer text out of a chat message.

    Some LM Studio builds of Qwen3.6 route the whole reply into
    `reasoning_content` and leave `content` empty (even with /no_think), so
    fall back to reasoning_content when content is blank.
    """
    content = (msg.content or "").strip()
    if content:
        return content
    return (getattr(msg, "reasoning_content", None) or "").strip()


def classify(article: Article) -> Verdict | None:
    """Judge one article against CRITERIA, returning None on any failure."""
    user = (
        f"SOURCE: {article.source}\nTITLE: {article.title}\n"
        f"SUMMARY: {article.summary}{think_suffix()}"
    )
    try:
        resp = client.chat.completions.create(
            model=config.CHAT_MODEL,
            temperature=0,
            messages=[
                {"role": "system", "content": CRITERIA},
                {"role": "user", "content": user},
            ],
            # If your LM Studio build rejects json_schema, swap for
            # response_format={"type": "json_object"} and the parse still works.
            response_format=cast(
                Any, {"type": "json_schema", "json_schema": VERDICT_SCHEMA}
            ),
        )
        return Verdict.from_json(json.loads(message_text(resp.choices[0].message)))
    except Exception as e:
        print(f"  ! classify failed: {e}", file=sys.stderr)
        return None


def embed(texts: list[str]) -> list[list[float]]:
    resp = client.embeddings.create(model=config.EMBED_MODEL, input=texts)
    return [d.embedding for d in resp.data]


def write_digest(items: list[Article]) -> str:
    payload = (
        "\n\n".join(
            f"[{it.category}] {it.title}\n{it.reason}\n{it.link}" for it in items
        )
        + think_suffix()
    )
    resp = client.chat.completions.create(
        model=config.CHAT_MODEL,
        temperature=0.7,
        messages=[
            {"role": "system", "content": DIGEST_PROMPT},
            {"role": "user", "content": payload},
        ],
    )
    return message_text(resp.choices[0].message)

"""The LM Studio / OpenAI-client wrapper: classify(), write_digest(), embed().

LM Studio speaks the OpenAI API, so we talk to it through the openai client
pointed at the LAN host. The model is local, so the api_key can be anything.
"""

from __future__ import annotations
import json
import re
import sys
from typing import Any, cast
from openai import OpenAI

from . import config
from .guardrails import answer_text, message_text, restore_links
from .models import Article, Verdict
from .prompts import CRITERIA, DIGEST_PROMPT, VERDICT_SCHEMA

client = OpenAI(base_url=config.BASE_URL, api_key="lm-studio")  # key can be anything


def think_suffix() -> str:
    """Qwen3: '/no_think' at the end of a prompt disables reasoning tokens."""
    return "" if config.THINKING else " /no_think"


def think_extra_body() -> dict[str, Any]:
    """Server-side thinking toggle for Qwen3 over the OpenAI API.

    The '/no_think' prompt suffix is unreliable on some Qwen3.6 builds, so also
    pass the chat template's `enable_thinking` flag, which the server applies
    when rendering the prompt. Belt-and-suspenders with think_suffix().
    """
    return {"chat_template_kwargs": {"enable_thinking": config.THINKING}}


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
            extra_body=think_extra_body(),
        )
        return Verdict.from_json(json.loads(message_text(resp.choices[0].message)))
    except Exception as e:
        print(f"  ! classify failed: {e}", file=sys.stderr)
        return None


def embed(texts: list[str]) -> list[list[float]]:
    resp = client.embeddings.create(model=config.EMBED_MODEL, input=texts)
    return [d.embedding for d in resp.data]


def write_digest(items: list[Article]) -> str:
    no_think_suffix = "" if config.DIGEST_THINKING else " /no_think"
    no_think_body = {"chat_template_kwargs": {"enable_thinking": config.DIGEST_THINKING}}
    payload = (
        "\n\n".join(
            f"[{it.category}] {it.title}\n{it.reason}\n@@{i}@@"
            for i, it in enumerate(items, 1)
        )
        + no_think_suffix
    )
    resp = client.chat.completions.create(
        model=config.CHAT_MODEL,
        temperature=0.7,
        max_tokens=config.DIGEST_MAX_TOKENS,
        messages=[
            {"role": "system", "content": DIGEST_PROMPT},
            {"role": "user", "content": payload},
        ],
        extra_body=no_think_body,
    )
    choice = resp.choices[0]
    text = answer_text(choice.message)
    if not text:
        raise RuntimeError(
            "model returned no digest text (it likely emitted only reasoning); "
            "disable thinking for the digest call."
        )
    if choice.finish_reason == "length":
        # Trim to the last complete @@N@@ marker so we don't return a half-written item.
        last = list(re.finditer(r"@@\d+@@", text))
        if last:
            text = text[: last[-1].end()]
            print(
                f"  ! digest hit the {config.DIGEST_MAX_TOKENS}-token cap; "
                f"returning {len(last)} of {len(items)} items",
                file=sys.stderr,
            )
        else:
            hint = (
                " (token cap hit mid-reasoning — try setting DIGEST_THINKING = True"
                " so reasoning tokens count against max_tokens properly, or raise"
                " DIGEST_MAX_TOKENS further)"
                if "<think>" in text
                else ""
            )
            raise RuntimeError(
                f"digest hit the {config.DIGEST_MAX_TOKENS}-token cap with no complete items{hint}"
            )
    return restore_links(text, items)

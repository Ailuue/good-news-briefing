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


# Qwen3 wraps reasoning in <think>...</think>. When a build inlines that into
# `content` instead of a separate reasoning_content field, strip it out so the
# chain-of-thought never reaches the reader.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def answer_text(msg: Any) -> str:
    """The model's FINAL answer only -- never its reasoning.

    Unlike message_text(), this deliberately does NOT fall back to
    reasoning_content: on builds that emit a chain-of-thought, that field holds
    raw thinking, which must never be surfaced to the reader. Blank content here
    means the model was still reasoning when it stopped, so there is no answer.
    """
    return _THINK_RE.sub("", msg.content or "").strip()


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


# Each item is tagged with an @@N@@ marker the model echoes onto the link line;
# we swap markers for real links after generation. The model paraphrases URLs
# (turning reasonstobecheerful.world into a dead reasonsbecheerful.world), so it
# never sees or writes a URL -- it only copies the opaque marker.
_LINK_MARK_RE = re.compile(r"@@(\d+)@@")


def _restore_links(text: str, items: list[Article]) -> str:
    """Replace @@N@@ markers with the real link for items[N-1]."""

    def sub(m: re.Match[str]) -> str:
        idx = int(m.group(1))
        return items[idx - 1].link if 1 <= idx <= len(items) else m.group(0)

    # Check the raw output (not the substituted result, which is all real links):
    # a URL here means the model wrote one despite being told to copy markers.
    if "http" in text:
        print("  ! digest contains a model-written URL (markers expected)", file=sys.stderr)
    restored = _LINK_MARK_RE.sub(sub, text)
    # Markers the model dropped or mangled instead of echoing them verbatim.
    if leftover := _LINK_MARK_RE.findall(restored):
        print(
            f"  ! digest left {len(leftover)} unresolved link marker(s)",
            file=sys.stderr,
        )
    return restored


def write_digest(items: list[Article]) -> str:
    payload = (
        "\n\n".join(
            f"[{it.category}] {it.title}\n{it.reason}\n@@{i}@@"
            for i, it in enumerate(items, 1)
        )
        + think_suffix()
    )
    resp = client.chat.completions.create(
        model=config.CHAT_MODEL,
        temperature=0.7,
        max_tokens=config.DIGEST_MAX_TOKENS,
        messages=[
            {"role": "system", "content": DIGEST_PROMPT},
            {"role": "user", "content": payload},
        ],
        extra_body=think_extra_body(),
    )
    choice = resp.choices[0]
    # Truncation mid-reasoning is exactly how the chain-of-thought leaked into a
    # briefing before. Fail loudly instead of returning a half-baked digest.
    if choice.finish_reason == "length":
        raise RuntimeError(
            f"digest hit the {config.DIGEST_MAX_TOKENS}-token cap before finishing; "
            "raise DIGEST_MAX_TOKENS or disable the model's thinking."
        )
    text = answer_text(choice.message)
    if not text:
        raise RuntimeError(
            "model returned no digest text (it likely emitted only reasoning); "
            "disable thinking for the digest call."
        )
    return _restore_links(text, items)

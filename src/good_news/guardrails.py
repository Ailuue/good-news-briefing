"""Guardrails over what the model returns.

Everything here acts on a model's raw output -- pulling out the answer,
stripping leaked chain-of-thought, and splicing real links back in -- so that
nothing the reader should never see (reasoning, hallucinated URLs) reaches the
digest. The request-side knobs (think_suffix, think_extra_body) live in llm.py.
"""

from __future__ import annotations
import re
import sys
from typing import Any

from .models import Article


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
    content = _THINK_RE.sub("", msg.content or "").strip()
    # If the token cap fired mid-reasoning, content starts with an unclosed
    # <think> tag (the regex above requires a closing tag to match). Return ""
    # so the caller sees "emitted only reasoning" rather than a confusing error.
    if content.startswith("<think>"):
        return ""
    return content


# Each item is tagged with an @@N@@ marker the model echoes onto the link line;
# we swap markers for real links after generation. The model paraphrases URLs
# (turning reasonstobecheerful.world into a dead reasonsbecheerful.world), so it
# never sees or writes a URL -- it only copies the opaque marker.
_LINK_MARK_RE = re.compile(r"@@(\d+)@@")


def restore_links(text: str, items: list[Article]) -> str:
    """Replace @@N@@ markers with the real link for items[N-1]."""

    def sub(m: re.Match[str]) -> str:
        idx = int(m.group(1))
        return items[idx - 1].link if 1 <= idx <= len(items) else m.group(0)

    # Check the raw output (not the substituted result, which is all real links):
    # a URL here means the model wrote one despite being told to copy markers.
    if "http" in text:
        print("  ! digest contains a model-written URL (markers expected)", file=sys.stderr)
    # Catch the model reusing the same marker number for every item, which would
    # make all links resolve to the same article.
    found = sorted(int(m) for m in _LINK_MARK_RE.findall(text))
    expected = list(range(1, len(items) + 1))
    if found != expected:
        print(
            f"  ! marker mismatch: expected @@1@@–@@{len(items)}@@ each once, "
            f"got {found}",
            file=sys.stderr,
        )
    restored = _LINK_MARK_RE.sub(sub, text)
    # Markers the model dropped or mangled instead of echoing them verbatim.
    if leftover := _LINK_MARK_RE.findall(restored):
        print(
            f"  ! digest left {len(leftover)} unresolved link marker(s)",
            file=sys.stderr,
        )
    return _space_items(restored)


def _space_items(text: str) -> str:
    """Guarantee a blank line after every link line.

    Each item is a sentence followed by its link; without a blank line between
    items markdown collapses a whole category into one run-on paragraph. The
    model's spacing is unreliable, so enforce it on the links we control rather
    than asking the model to get it right.
    """
    text = re.sub(r"(?m)^(https?://\S+)[ \t]*\n+", r"\1\n\n", text)
    return text.rstrip("\n")

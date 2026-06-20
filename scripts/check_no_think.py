"""Find a reasoning-disable strategy that works for the loaded chat model.

Different model families silence chain-of-thought differently. Qwen3 honours
"/no_think" + chat_template_kwargs.enable_thinking; Gemma and others ignore
those and may need a different flag (or have no off switch at all). This probes
the model named in config.CHAT_MODEL with several strategies and reports how
many reasoning tokens each one leaves behind, so you can pick the one that
actually works before running the full pipeline.

Run it after loading a model in LM Studio. Requires PC_HOST set in .env or env.
Exits 0 if at least one strategy fully suppresses reasoning, 1 otherwise.
"""
import sys

from good_news import config
from openai import OpenAI

from good_news.prompts import CRITERIA

client = OpenAI(base_url=config.BASE_URL, api_key="lm-studio")
MODEL = config.CHAT_MODEL

# Use the *real* classify prompt: a trivial puzzle doesn't trip this model into
# reasoning, but judging an article against CRITERIA does. We want to know which
# flag (if any) stops THAT reasoning.
SAMPLE = (
    "SOURCE: positive.news\n"
    "TITLE: Community restores a polluted river to life\n"
    "SUMMARY: Volunteers spent two years clearing a local river of waste; fish "
    "and birds have returned and the town now swims there again."
)

# Markers that mean the model reasoned. This build uses a channel format
# (<|channel>thought ...) inline in content; older builds use <think> or a
# separate reasoning_content field. Detect all three.
_REASON_MARKERS = ("<|channel", "<think>", "channel>thought")

# (label, prompt_suffix, extra_body). Add a row here to test another flag.
STRATEGIES: list[tuple[str, str, dict]] = [
    ("baseline (no flags)", "", {}),
    ("/no_think suffix", " /no_think", {}),
    ("enable_thinking=False", "", {"chat_template_kwargs": {"enable_thinking": False}}),
    ("both (current Qwen path)", " /no_think", {"chat_template_kwargs": {"enable_thinking": False}}),
    ("reasoning_effort=none", "", {"reasoning_effort": "none"}),
    ("thinking=False", "", {"chat_template_kwargs": {"thinking": False}}),
]


def reasoning_chars(suffix: str, extra_body: dict) -> tuple[int, str]:
    """Return (reasoning char count, finish_reason) for one strategy.

    Counts reasoning whether it lands in reasoning_content or inline in content
    as a channel/think block; returns 0 only when the model went straight to the
    answer with no reasoning markers at all.
    """
    resp = client.chat.completions.create(
        model=MODEL,
        temperature=0,
        max_tokens=512,
        messages=[
            {"role": "system", "content": CRITERIA},
            {"role": "user", "content": SAMPLE + suffix},
        ],
        extra_body=extra_body,
    )
    choice = resp.choices[0]
    content = choice.message.content or ""
    reasoning = getattr(choice.message, "reasoning_content", None) or ""
    inline = content if any(m in content for m in _REASON_MARKERS) else ""
    return len(reasoning) + len(inline), choice.finish_reason


print(f"Probing reasoning suppression for: {MODEL}\n")
clean = []
for label, suffix, extra_body in STRATEGIES:
    try:
        n, finish = reasoning_chars(suffix, extra_body)
    except Exception as e:  # a build may reject an unknown extra_body key
        print(f"  {label:28} ERROR: {type(e).__name__}: {e}")
        continue
    status = "clean" if n == 0 else f"{n} reasoning chars"
    print(f"  {label:28} {status:22} finish={finish}")
    if n == 0:
        clean.append(label)

if clean:
    print(f"\nRESULT: suppressed by -> {', '.join(clean)}")
    print("Wire the winning strategy into llm._model_family / think_extra_body.")
    sys.exit(0)
else:
    print("\nRESULT: no strategy fully suppressed reasoning.")
    print("This GGUF may be a thinking-only build; consider a non-thinking quant.")
    sys.exit(1)

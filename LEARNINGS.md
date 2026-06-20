# Engineering Learnings

A running log of the non-obvious lessons this project has taught me — things I
worked out by building and measuring, not by reading about them. Each entry
records what I observed, what I changed, what the numbers did, and the
general principle I'm taking forward. In chronological order, oldest first.

Each entry follows the same shape:

- **Context** — what I was doing and why.
- **Finding** — what I observed, with evidence.
- **Action** — what I changed in response.
- **Result** — what happened, in numbers where possible.
- **Takeaway** — the transferable principle.

---

## 2026-06-19 — The most dangerous failure mode is the one that returns zero instead of raising

**Context.** Early on the pipeline would occasionally just produce an empty
briefing with no error — every feed quietly yielding nothing.

**Finding.** Two independent "succeeds with zero results" traps, both upstream of
any code I'd written. (1) macOS Python often ships without root certificates, so
feedparser's urllib fails *every* HTTPS feed with `CERTIFICATE_VERIFY_FAILED` and
returns zero entries — no exception. (2) feedparser itself never raises on a
broken or unreachable feed: it swallows network, SSL, and parse errors into a
`d.bozo` flag and hands back an empty `entries` list. Both look identical to "the
feed legitimately had no new items."

**Action.** Point urllib at certifi's CA bundle at import time, before any feed is
parsed ([`config.py`](src/good_news/config.py)), and warn loudly if certifi is
missing. In the fetch loop, check `d.bozo` and surface `d.bozo_exception` when a
feed comes back empty rather than counting it as zero
([`sources.py`](src/good_news/sources.py)).

**Takeaway.**

- A library that returns an empty result on failure is more dangerous than one
  that throws, because the empty result flows downstream looking like valid data.
  When integrating one, hunt for its silent-failure flag (`d.bozo` here) and
  convert it into a visible warning yourself.
- Environment assumptions (CA certs present) belong in one-time setup that runs
  *before* the first network call and announces when its precondition is missing —
  not in a comment hoping the next machine is configured the same way.

---

## 2026-06-19 — Keep data the model can mangle out of the model's hands entirely

**Context.** The digest is one model-written paragraph per story, each ending with
the article's link. The obvious design is to hand the model each item's URL and
ask it to place the link under its sentence.

**Finding.** The model paraphrases URLs. It would turn
`reasonstobecheerful.world` into a plausible-looking but dead
`reasonsbecheerful.world` — a silently broken link in something a reader is meant
to click. Asking nicely ("copy the URL exactly") doesn't fix a model that treats
text as something to rewrite.

**Action.** The model never sees a URL. Each item is tagged with an opaque
`@@N@@` marker; the model only copies that marker onto the link line, and
[`restore_links()`](src/good_news/guardrails.py) swaps each marker for the real
URL *after* generation, in code. The same step audits the raw output for the
failure modes that remain — a literal `http` (the model wrote a URL anyway), a
reused or missing marker number, leftover unresolved markers — and warns on each.

**Takeaway.**

- If a value must be exact and you can supply it yourself, don't route it through
  the generative step at all. Give the model an opaque token to echo and splice the
  real value back deterministically. This generalizes to IDs, citations, prices,
  any verbatim string.
- A generative pipeline needs a *verification* stage that inspects the raw output
  for the specific ways this model misbehaves, not just a hope that the prompt held.

---

## 2026-06-19 — A reasoning model leaks its chain-of-thought two different ways, and the same output needs two different extraction rules

**Context.** The local model is Qwen3, which emits reasoning tokens before its
answer. Two of my calls consume that output: `classify()` needs the JSON verdict,
and `write_digest()` needs the prose briefing. I assumed "turn thinking off" with
the documented `/no_think` prompt suffix would be the end of it.

**Finding.** Two surprises. First, *disabling* thinking is unreliable: some LM
Studio builds of Qwen3 ignore the `/no_think` suffix, and some route the entire
reply into a separate `reasoning_content` field leaving `content` empty. Second,
when reasoning *does* leak it arrives by two different paths — either inlined into
`content` wrapped in `<think>...</think>` tags, or in the separate
`reasoning_content` field — and the two call sites need *opposite* handling of it.
For `classify()`, the JSON is the answer and sometimes lands in
`reasoning_content`, so I must fall back to that field to find it. For the digest,
`reasoning_content` is raw thinking that must *never* reach the reader, so falling
back to it would be exactly wrong.

**Action.** Belt-and-suspenders on the request side: send both the `/no_think`
suffix *and* the server-side `chat_template_kwargs: {enable_thinking: False}` flag
([`llm.py`](src/good_news/llm.py)). On the response side, two functions with
deliberately different rules ([`guardrails.py`](src/good_news/guardrails.py)):
`message_text()` falls back to `reasoning_content` (used where that field may hold
the real answer); `answer_text()` strips `<think>` blocks and refuses to fall back
(used for reader-facing text — blank means "still reasoning," not "look in the
reasoning field").

**Takeaway.**

- "The answer" and "the final answer the reader sees" are two different
  extractions of the same model output, and conflating them is how raw
  chain-of-thought ends up in front of a user. Name and separate them.
- Treat a vendor's thinking toggle as advisory, not load-bearing. Set it by every
  mechanism available (prompt suffix *and* server flag) and still validate the
  output, because builds disagree about which mechanism they honor.

---

## 2026-06-19 — `max_tokens` budgets the reasoning tokens too, so cap failures need a known cut-point

**Context.** Long digests were truncating mid-sentence. I set an explicit
`DIGEST_MAX_TOKENS` because, without one, LM Studio applies its own short default
limit. Then individual runs started *erroring* instead of truncating cleanly.

**Finding.** Two coupled problems. (1) When the completion hits the cap
(`finish_reason == "length"`), naively returning the text yields a half-written
final item. (2) More subtly: if thinking is left on, the reasoning tokens count
against the *same* `max_tokens` budget — so the model can burn the entire cap
*before writing any answer*, leaving output that is nothing but an unclosed
`<think>` block. Raising the token limit doesn't help; the reasoning just expands
to fill it.

**Action.** On a length finish, trim back to the last complete `@@N@@` marker so
the digest ends on a whole item, and log how many of N items survived
([`llm.py`](src/good_news/llm.py)). If *no* complete item exists and the text is
an unclosed `<think>` block, raise with a pointed hint — disable digest thinking
so output tokens aren't eaten by reasoning, rather than blindly raising the cap.
`DIGEST_THINKING` is forced off for the digest for exactly this reason: it's
creative writing, not analysis, so reasoning spends budget without improving the
result.

**Takeaway.**

- `max_tokens` is a budget over *everything the model emits*, reasoning included.
  On a thinking model an output-length failure can mean "spent it all thinking,"
  which a bigger budget won't fix — diagnose *what* filled the budget before
  raising it.
- When a length cap is possible, design the output so there's a safe place to cut.
  The `@@N@@` markers gave truncation a clean item boundary to fall back to instead
  of a ragged half-sentence.

---

## 2026-06-20 — Independent calls can't leak order, but the fixture file still can

**Context.** While building the optimism eval, I'd written the 20 reference
cases into [`evals/optimism_fixtures.json`](evals/optimism_fixtures.json) in
neatly descending score order. I asked myself whether the model could pick up on
that ordering and "cheat."

**Finding.** In this harness it can't: `classify()` is called once per article,
each call a fresh, stateless request at `temperature=0` with only that one
article in the prompt. The model never sees the fixtures as a sequence, so file
order cannot influence its scores. But the ordering still mattered for two other
reasons — it can anchor *me* while labeling (each score drifting off the last
instead of being judged on its own), and it becomes a tell if the file is ever
fed as a batch or read by an LLM-as-judge later.

**Action.** Shuffled the fixtures so the file is non-monotonic, while leaving the
runner to report results in file order.

**Takeaway.** Separate "can this leak into the system *as built*" from "is this a
latent hazard." The first was a no — the architecture made it impossible. The
second was a yes, and cost nothing to fix. Reasoning about data-leakage means
reasoning about the exact call boundary, not a vague feeling that "the model
sees the file."

---

## 2026-06-20 — An LLM will quietly compress a rating scale unless you anchor it

**Context.** The classifier ([`src/good_news/llm.py`](src/good_news/llm.py))
asks a local model to score each story's `optimism` from 0.0 to 1.0. Unit tests
mock the model, so they verify plumbing but never whether the *judgement* is any
good. To probe that, I built an agentic eval
([`evals/run_optimism_eval.py`](evals/run_optimism_eval.py)): I hand-labeled 20
articles with reference optimism scores spread across the full range, then had
the eval pass a case only when the model landed within ±0.1 of my score.

**Finding.** The model scored **6/20**, with a mean absolute error of **0.242**
and a mean *signed* error of **+0.222** — consistently optimistic. The real
problem wasn't the bias, it was the shape: the model collapsed almost every
mid-to-high story onto a single value, **0.85**. A regional clean-water project,
a one-off river cleanup, a single coffee shop unionizing, and an honorary
"volunteer day" all came back 0.85 — the same score as genuine national wins. It
could tell *great* from *barely-news* at the extremes but was effectively blind
across the entire middle, which is where most real stories live. The original
prompt only said "a vague positive-sounding headline scores low" — no anchors,
so the model had no idea what a 0.4 versus a 0.6 was supposed to look like.

**Action.** I rewrote only the `SCORING` block in
[`src/good_news/prompts.py`](src/good_news/prompts.py): five labeled bands
(0.10–0.25 up to 0.90–1.00), each with concrete examples, plus two explicit
instructions — *reserve the top of the range* and *when between bands, lean
lower if the good is mostly announced, symbolic, or tiny*. No code changes.

**Result.**

| Metric | Before | After |
|---|---|---|
| Within ±0.1 of reference | 6/20 | **13/20** |
| Mean absolute error | 0.242 | **0.095** |
| Mean signed error | +0.222 | **+0.044** |

The plateau broke: scores now spread across 0.15–0.95 and the systematic
optimism nearly vanished. Remaining misses clustered on "announced but not yet
delivered" stories (pledges, grants, pilots) the model still floors around 0.55
— and a few of those are genuine judgement disagreement rather than model error.

**Takeaway.**

- A model handed an unanchored numeric scale will use a fraction of it and bunch
  scores together. The fix is calibration anchors with concrete examples per
  band, not adjectives like "high" and "low."
- **Test the judgement, not just the plumbing.** Mocked unit tests would never
  have surfaced this; it only showed up because the eval measured the model
  against considered reference labels.
- **Spread your reference labels deliberately.** Clustered ground truth can't
  reveal whether a model discriminates — the failure was only visible because
  the references covered the whole 0.2–0.95 range.
- **Watch signed error, not just absolute error.** Mean *absolute* error says
  "how wrong"; mean *signed* error revealed the wrongness had a direction (a
  fixable bias) rather than being random noise.
- **Know when to stop.** At 13/20 I stopped tuning the prompt: chasing the last
  few cases would have meant overfitting the prompt to my 20 specific labels
  rather than improving real calibration.

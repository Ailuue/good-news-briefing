# Engineering Learnings

A running log of the non-obvious lessons this project has taught me — things I
worked out by building and measuring, not by reading about them. Each entry
records what I observed, what I changed, what the numbers did, and the
general principle I'm taking forward. Newest entries first.

Each entry follows the same shape:

- **Context** — what I was doing and why.
- **Finding** — what I observed, with evidence.
- **Action** — what I changed in response.
- **Result** — what happened, in numbers where possible.
- **Takeaway** — the transferable principle.

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

# Tests

Fast, deterministic unit tests for the pipeline. **No network, no GPU, no
LM Studio** — the model is faked at its boundary, so the whole suite runs in
well under a second.

## Running

```bash
pip install -e ".[test]"   # one-time: installs pytest
pytest                     # run everything
pytest tests/test_llm.py   # one file
pytest -k restore_links    # tests matching a name
pytest -v                  # show each test name
```

## The one idea to copy

The model talks to us through OpenAI-shaped response objects. To test our code
without a running model, we hand-build those objects with `fake_chat()` /
`fake_message()` (in `conftest.py`) and swap the real client out:

```python
install_fake_client(monkeypatch, fake_chat(content='{"is_good_news": true}'))
verdict = llm.classify(article)   # runs our real parsing against canned output
```

See `test_llm.py` — that's the template for testing any new model call.

## What's here

| File | Covers |
|------|--------|
| `test_guardrails.py` | Stripping leaked `<think>` reasoning, splicing real links over `@@N@@` markers (regression tests for past bugs) |
| `test_models.py` | `Verdict.from_json` tolerating messy/partial model JSON |
| `test_pipeline.py` | `keep()` editorial filter, `cosine()`, `dedupe()` |
| `test_sources.py` | Reddit URL extraction, HTML→text, crawl with network mocked |
| `test_store.py` | The SQLite seen-store (uses a temp DB) |
| `test_llm.py` | `classify()` / `write_digest()` with the model faked |

## Tests vs. evals

These are **tests**: "is my *code* correct?" — model mocked, deterministic, run
on every change. They do **not** measure whether the model judges news well —
that's an **eval**: run the *real* model over a labeled set of articles and
score its verdicts. Keep evals separate (e.g. an `evals/` dir) so they never
block CI.

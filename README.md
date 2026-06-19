# Good News Briefing

A small, self-hosted pipeline that pulls a set of RSS feeds, uses a **local LLM**
(served by [LM Studio](https://lmstudio.ai/)) to judge each story against a
tunable editorial point of view, collapses duplicate coverage, and composes a
warm Markdown "good news" briefing — optionally emailing it to you and a friend.

Nothing leaves your network except the RSS fetches and the outgoing email: the
classification and writing all happen on your own GPU.

## How it works

1. **Fetch** — pull a list of RSS feeds (`feedparser`).
2. **Dedupe history** — skip anything seen on a previous run (SQLite).
3. **Classify** — ask your local chat model to judge each item against the
   editorial criteria, returning structured JSON.
4. **Filter** — drop corporate PR and pure-luck fluff; keep genuine good news
   above an optimism threshold.
5. **Collapse duplicates** — use a local embedding model to merge near-identical
   coverage of the same story (degrades gracefully if no embed model is loaded).
6. **Compose** — have the model write a calm, grouped Markdown briefing.
7. **Deliver** — save it to `~/good-news/`, open it (macOS), and optionally email it.

## Requirements

- A machine running **LM Studio** with its server enabled and **bound to
  `0.0.0.0`**, serving a chat model and (optionally) an embedding model.
  Defaults are tuned for an RTX 3090: `qwen3.6-35b-a3b` (IQ4_XS) +
  `qwen3-embedding-0.6b`.
- Python 3.10+ on the machine that runs this script (it talks to LM Studio over
  the LAN, so it can be a different computer).

## Setup

```bash
git clone <your-repo-url>
cd good-news-feed
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then edit .env (see below)
```

Edit `.env` with your own values:

| Variable             | Purpose                                                        |
| -------------------- | -------------------------------------------------------------- |
| `PC_HOST`            | LAN IP of the machine running LM Studio (e.g. `192.168.1.106`) |
| `EMAIL_FROM`         | Sender Gmail address (leave blank to disable email)            |
| `EMAIL_TO`           | Comma-separated recipients                                     |
| `GMAIL_APP_PASSWORD` | Gmail **app password** (not your normal password)              |
| `SMTP_HOST`/`SMTP_PORT` | Optional overrides (default Gmail SSL)                      |

In LM Studio: load your chat and embedding models, start the server, and bind it
to `0.0.0.0` so other machines on the LAN can reach it. Copy the exact model ids
from the server panel into `CHAT_MODEL` / `EMBED_MODEL` in `src/good_news/config.py`
if they differ from the defaults.

## Usage

```bash
# Safe test: ignores seen-history, prints the digest, saves/sends nothing
python3 good_news_briefing.py --dry-run

# Show every keep/drop decision and the model's reasoning (great for tuning)
python3 good_news_briefing.py --dry-run --verdicts

# Real run: saves ~/good-news/briefing-YYYY-MM-DD.md, opens it, and emails it
python3 good_news_briefing.py
```

### Flags

| Flag         | Effect                                                                 |
| ------------ | ---------------------------------------------------------------------- |
| `--dry-run`  | Ignore seen-history, print to terminal, save/open/email nothing        |
| `--limit N`  | Entries to pull per feed (default: 5 in `--dry-run`, else 25)          |
| `--verdicts` | Print each article's keep/drop decision and reason                     |
| `--email`    | Also send the email during a `--dry-run` (for testing the email path)  |
| `--no-email` | Suppress the email on a real run                                       |

## Email setup (Gmail)

Gmail's SMTP needs an **app password**, not your normal password:

1. Enable 2-Step Verification on the account.
2. Create one at <https://myaccount.google.com/apppasswords>.
3. Put the 16 characters in `.env` as `GMAIL_APP_PASSWORD` (or export it in your
   shell). If it's unset, the script skips emailing with a warning instead of
   failing.

## Configuration

The code lives in the `src/good_news/` package; the knobs worth tuning live in
two files:

- `src/good_news/prompts.py`
  - **`CRITERIA`** — the editorial point of view: what counts as "good news."
    This is the one knob worth rewriting to taste.
- `src/good_news/config.py`
  - **`FEEDS`** — the list of RSS sources.
  - `CHAT_MODEL`, `EMBED_MODEL`, `THINKING` — model ids and the Qwen reasoning toggle.
  - `OPTIMISM_THRESHOLD`, `DEDUPE_SIMILARITY`, `MAX_PER_CATEGORY`, `MAX_ENTRIES_PER_FEED`.

Run it with `python3 good_news_briefing.py ...` from a source checkout, or
`pip install -e .` and then use `python3 -m good_news ...` (or the `good-news`
console script).

## Testing & evals

**Unit tests** (fast, no model required):

```bash
pip install -e ".[test]"
pytest
```

Tests mock the LLM at its boundary and run in under a second. See
`tests/README.md` for details.

**Classifier eval** (requires LM Studio running):

```bash
python evals/run_eval.py             # summary — failures only
python evals/run_eval.py --verbose   # show all 25 cases including passes
```

Runs `classify()` against 25 hand-labeled articles and reports per-field
accuracy. Use this after editing `CRITERIA` to check for regressions. Exit
code is non-zero if any case fails.

## Scripts

`scripts/check_no_think.py` — verifies that `/no_think` and `enable_thinking:false`
actually suppress reasoning tokens on your model build before you run the
full pipeline. Useful when setting up a new model or after an LM Studio update:

```bash
python scripts/check_no_think.py
```

## Output

- `~/good-news/briefing-YYYY-MM-DD.md` — the briefing for each real run.
- `~/good-news/seen.sqlite3` — dedupe history so future runs only surface new items.

## Scheduling (optional)

Run it automatically in the evening with `cron` or `launchd` on macOS, e.g. a
crontab line for 6pm daily:

```cron
0 18 * * * cd /path/to/good-news-feed && .venv/bin/python good_news_briefing.py >> ~/good-news/cron.log 2>&1
```

## Privacy

`.env` (your IP, email addresses, and app password) is gitignored and never
committed. Only safe placeholder defaults live in the source.

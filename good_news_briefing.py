#!/usr/bin/env python3
"""
Good News Evening Briefing
--------------------------
Runs on your Mac, calls your PC's LM Studio server over the LAN.
Designed to run in the evening (Taiwan time), catching the start of the US news day.

Pipeline:
  1. Pull a set of RSS feeds.
  2. Skip anything seen on a previous run (SQLite).
  3. Ask your local model to judge each item against a tunable editorial
     point of view, returning structured JSON.
  4. Drop corporate PR and pure-luck fluff; keep genuine good news.
  5. Use your local embedding model to collapse duplicate coverage of the
     same story (degrades gracefully if no embed model is loaded).
  6. Have the model compose a warm markdown briefing and open it.

Setup:
  pip install -r requirements.txt
  Copy .env.example to .env and fill in PC_HOST and (optionally) the email
  settings. Set CHAT_MODEL / EMBED_MODEL below. In LM Studio, start the server
  and bind it to 0.0.0.0 so the Mac can reach it.
"""

from __future__ import annotations
import sqlite3
import json
import datetime
import pathlib
import subprocess
import sys
import math
import os
from typing import Any, cast
from openai import OpenAI
import feedparser

# macOS Python often ships without root certificates, so feedparser's urllib
# fails every HTTPS feed with CERTIFICATE_VERIFY_FAILED and silently returns
# zero entries. Point urllib at certifi's CA bundle before any feed is parsed.
try:
    import certifi

    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except ImportError:
    print("  ! certifi not installed; HTTPS feeds may fail (pip install certifi)", file=sys.stderr)

# Load private/local settings from a .env file next to this script (gitignored).
# Real environment variables take precedence, so nothing here is required.
try:
    from dotenv import load_dotenv

    load_dotenv(pathlib.Path(__file__).with_name(".env"))
except ImportError:
    print("  ! python-dotenv not installed; relying on real env vars (pip install python-dotenv)", file=sys.stderr)


def _env_list(name: str, default: str = "") -> list[str]:
    """Read a comma-separated env var into a clean list of strings."""
    return [x.strip() for x in os.environ.get(name, default).split(",") if x.strip()]


# ----------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------
PC_HOST = os.environ.get("PC_HOST", "127.0.0.1")  # set in .env to your PC's LAN IP
BASE_URL = f"http://{PC_HOST}:1234/v1"
# Tuned for an RTX 3090 (24GB). Qwen3.6-35B-A3B is a 3B-active MoE: fast and
# high-quality. Use the IQ4_XS quant (~18GB) so it stays fully on the card and
# leaves room for context + the embedding model. Copy the EXACT ids from the
# LM Studio server panel after loading each model.
CHAT_MODEL = "qwen3.6-35b-a3b"  # load the IQ4_XS (or MTP) GGUF in LM Studio
EMBED_MODEL = "qwen3-embedding-0.6b"  # tiny; co-loads with the chat model fine

# Qwen3 reasoning toggle. False appends "/no_think" so the model skips reasoning
# tokens -- much faster, which is what you want for high-volume classification.
# Set True only if you ever want it to deliberate (slower).
THINKING = False

MAX_PER_CATEGORY = 4
OPTIMISM_THRESHOLD = 0.55  # 0..1; raise to be pickier
DEDUPE_SIMILARITY = 0.86  # cosine above this = treat as the same story
MAX_ENTRIES_PER_FEED = 25
OUT_DIR = pathlib.Path.home() / "good-news"
DB_PATH = OUT_DIR / "seen.sqlite3"
OPEN_WHEN_DONE = True  # `open` the file on macOS when finished

# ---- Email (optional) -----------------------------------------------------
# After a real run, mail the briefing to EMAIL_TO. Leave EMAIL_TO empty to
# disable. Auth uses a Gmail APP PASSWORD (not your normal password), read from
# the GMAIL_APP_PASSWORD env var so no secret lives in this file:
#   1. Turn on 2-Step Verification for the Gmail account.
#   2. Visit https://myaccount.google.com/apppasswords, create one, copy 16 chars.
#   3. Add to ~/.zshrc:  export GMAIL_APP_PASSWORD="xxxxxxxxxxxxxxxx"
EMAIL_FROM = os.environ.get("EMAIL_FROM", "")  # set in .env
EMAIL_TO = _env_list("EMAIL_TO")  # set in .env, comma-separated
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))  # SSL

# Verify each of these resolves in a browser; RSS paths drift over time.
FEEDS = [
    # progressive / social-progress
    "https://www.theguardian.com/inequality/rss",
    "https://www.propublica.org/feeds/propublica/main",
    "https://theintercept.com/feed/?lang=en",
    "https://www.motherjones.com/feed/",
    "https://www.commondreams.org/rss.xml",
    # anti-corporate / tech
    "https://www.404media.co/rss/",
    "https://www.eff.org/rss/updates.xml",
    # solutions / good news
    "https://reasonstobecheerful.world/feed/",
    "https://www.positive.news/feed/",
    "https://www.yesmagazine.org/feed",
    # community / humans helping humans
    "https://www.reddit.com/r/UpliftingNews/.rss",
]

# ----------------------------------------------------------------------
# EDITORIAL POINT OF VIEW  --  this is the one knob worth tuning.
# Everything about what counts as "good" lives here. Rewrite freely.
# ----------------------------------------------------------------------
CRITERIA = """You are the editorial filter for a personal "good news" morning briefing.
Judge each news item against the point of view below and return ONLY the JSON the schema asks for.
Be honest and consistent; when genuinely unsure, lean toward excluding.

WHAT COUNTS AS GOOD NEWS HERE
- Genuine progress, not merely the absence of something bad: something measurably improved for
  people, communities, workers, or the planet.
- Political and social stories judged from a progressive, left-leaning frame: expansions of civil
  and human rights, social equality, fairness, democratic participation, public health, poverty
  reduction, and climate progress are positive. Accountability for the powerful is positive.
- Anti-corporate accountability counts as good: antitrust action, unionization and strikes won,
  worker protections, regulation that shields people or the environment, consumer and privacy wins,
  and exposure of corporate wrongdoing.
- In technology, judged against corporate power: privacy protections, open-source and
  decentralization wins, right-to-repair, and pushback against surveillance, monopoly, or
  exploitative platforms are positive.
- "Humans helping humans": mutual aid, communities rallying around someone in need, rescues,
  organized generosity, solidarity, and volunteering.

WHAT TO EXCLUDE (set is_good_news to false)
- Pure luck with no human kindness at its core (lottery wins, finding money, freak good fortune).
  Mark is_pure_luck true. Allow such a story ONLY if its heart is people choosing to help other
  people, in which case category is community_helping.
- Corporate self-congratulation: PR-driven "good deeds", greenwashing, an executive's charitable
  gesture that mainly serves the brand. Mark is_corporate_pr true.
- News that is good for a company, market, or executive but neutral-to-bad for ordinary people or
  workers (record profits, stock jumps, splashy product launches).
- Negative, fear-driven, or simply neutral news.
- Framing settled injustices as mere "controversy" or "both sides".

SCORING
- optimism is 0.0 to 1.0: how genuinely uplifting and substantive the good is. A small but real
  human-helping-human story can score high; a vague positive-sounding headline scores low.
- category is your single best fit from the allowed list.
"""

# ----------------------------------------------------------------------
client = OpenAI(base_url=BASE_URL, api_key="lm-studio")  # key can be anything


def think_suffix() -> str:
    """Qwen3: '/no_think' at the end of a prompt disables reasoning tokens."""
    return "" if THINKING else " /no_think"


def message_text(msg) -> str:
    """Pull the answer text out of a chat message.

    Some LM Studio builds of Qwen3.6 route the whole reply into
    `reasoning_content` and leave `content` empty (even with /no_think), so
    fall back to reasoning_content when content is blank.
    """
    content = (msg.content or "").strip()
    if content:
        return content
    return (getattr(msg, "reasoning_content", None) or "").strip()


VERDICT_SCHEMA = {
    "name": "verdict",
    "schema": {
        "type": "object",
        "properties": {
            "is_good_news": {"type": "boolean"},
            "category": {
                "type": "string",
                "enum": [
                    "politics_social",
                    "anti_corporate",
                    "technology",
                    "community_helping",
                    "science_health",
                    "environment",
                    "other",
                ],
            },
            "optimism": {"type": "number"},
            "is_corporate_pr": {"type": "boolean"},
            "is_pure_luck": {"type": "boolean"},
            "reason": {"type": "string"},
        },
        "required": [
            "is_good_news",
            "category",
            "optimism",
            "is_corporate_pr",
            "is_pure_luck",
            "reason",
        ],
    },
}

DIGEST_PROMPT = """You are writing a warm, concise evening briefing of good news for one reader who
may read it to unwind before bed or save it for the next morning.
Group the items under short thematic headers. For each item, write one or two sentences in your own
words about why it is encouraging, then put the link on its own line. Be genuine and grounded, never
saccharine or patronizing. Keep the tone calm and steadying rather than activating. Open with a
single short line that sets a hopeful, restful tone.
Output Markdown only."""


def init_db() -> sqlite3.Connection:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS seen(link TEXT PRIMARY KEY, ts TEXT)")
    return con


def is_seen(con, link: str) -> bool:
    return (
        con.execute("SELECT 1 FROM seen WHERE link=?", (link,)).fetchone() is not None
    )


def mark_seen(con, link: str) -> None:
    con.execute(
        "INSERT OR IGNORE INTO seen(link, ts) VALUES(?, ?)",
        (link, datetime.datetime.now().isoformat()),
    )


def fetch(per_feed: int = MAX_ENTRIES_PER_FEED) -> list[dict]:
    out = []
    for url in FEEDS:
        try:
            d: Any = feedparser.parse(url)
        except Exception as e:
            print(f"  ! feed failed {url}: {e}", file=sys.stderr)
            continue
        # feedparser swallows network/SSL/parse errors into d.bozo rather than
        # raising, so warn (instead of silently fetching 0) when a feed breaks.
        if d.bozo and not d.entries:
            print(f"  ! feed error {url}: {d.bozo_exception!r}", file=sys.stderr)
            continue
        src = d.feed.get("title", url)
        for e in d.entries[:per_feed]:
            out.append(
                {
                    "title": (e.get("title") or "").strip(),
                    "summary": (e.get("summary") or e.get("description") or "")[:1200],
                    "link": e.get("link", ""),
                    "source": src,
                }
            )
    return out


def classify(title: str, summary: str, source: str) -> dict | None:
    user = f"SOURCE: {source}\nTITLE: {title}\nSUMMARY: {summary}{think_suffix()}"
    try:
        resp = client.chat.completions.create(
            model=CHAT_MODEL,
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
        return json.loads(message_text(resp.choices[0].message))
    except Exception as e:
        print(f"  ! classify failed: {e}", file=sys.stderr)
        return None


def keep(v: dict | None) -> bool:
    if not v or not v.get("is_good_news"):
        return False
    if v.get("is_corporate_pr"):
        return False
    if v.get("is_pure_luck") and v.get("category") != "community_helping":
        return False
    return float(v.get("optimism", 0)) >= OPTIMISM_THRESHOLD


def embed(texts: list[str]) -> list[list[float]]:
    resp = client.embeddings.create(model=EMBED_MODEL, input=texts)
    return [d.embedding for d in resp.data]


def cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def dedupe(items: list[dict]) -> list[dict]:
    """Collapse near-identical coverage, keeping the highest-optimism version."""
    if len(items) < 2:
        return items
    try:
        vecs = embed([it["title"] for it in items])
    except Exception as e:
        print(f"  ! embeddings unavailable, skipping dedupe: {e}", file=sys.stderr)
        return items
    kept, kept_vecs = [], []
    for it, v in sorted(zip(items, vecs), key=lambda p: -p[0]["optimism"]):
        if all(cosine(v, kv) < DEDUPE_SIMILARITY for kv in kept_vecs):
            kept.append(it)
            kept_vecs.append(v)
    return kept


def write_digest(items: list[dict]) -> str:
    payload = (
        "\n\n".join(
            f"[{it['category']}] {it['title']}\n{it['reason']}\n{it['link']}"
            for it in items
        )
        + think_suffix()
    )
    resp = client.chat.completions.create(
        model=CHAT_MODEL,
        temperature=0.7,
        messages=[
            {"role": "system", "content": DIGEST_PROMPT},
            {"role": "user", "content": payload},
        ],
    )
    return message_text(resp.choices[0].message)


def send_email(subject: str, markdown_text: str) -> None:
    """Mail the briefing via Gmail SMTP. No-ops (with a warning) if unconfigured."""
    import smtplib
    import ssl
    from email.message import EmailMessage

    recipients = [a for a in EMAIL_TO if a and "@" in a and "example.com" not in a]
    if not EMAIL_FROM or not recipients:
        print(
            "  ! email skipped: set EMAIL_FROM and EMAIL_TO in your .env",
            file=sys.stderr,
        )
        return
    password = os.environ.get("GMAIL_APP_PASSWORD")
    if not password:
        print(
            "  ! email skipped: GMAIL_APP_PASSWORD not set (see EMAIL config notes)",
            file=sys.stderr,
        )
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = ", ".join(recipients)
    msg.set_content(markdown_text)  # plain-text fallback for text-only clients
    try:
        import markdown as _md

        html = _md.markdown(markdown_text)
        msg.add_alternative(f"<html><body>{html}</body></html>", subtype="html")
    except ImportError:
        print(
            "  ! 'markdown' not installed; sending plain text only (pip install markdown)",
            file=sys.stderr,
        )

    try:
        # SSL_CERT_FILE was pointed at certifi above, so the default context
        # verifies Gmail's cert cleanly here too.
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx) as s:
            s.login(EMAIL_FROM, password)
            s.send_message(msg)
        print(f"  emailed briefing to {', '.join(recipients)}", file=sys.stderr)
    except Exception as e:
        print(f"  ! email failed: {e}", file=sys.stderr)


def main(
    dry_run: bool = False,
    limit: int | None = None,
    show_verdicts: bool = False,
    send_mail: bool = False,
) -> None:
    con = None if dry_run else init_db()
    per_feed = limit if limit is not None else (5 if dry_run else MAX_ENTRIES_PER_FEED)

    articles = fetch(per_feed=per_feed)
    print(f"fetched {len(articles)} articles", file=sys.stderr)

    if dry_run:
        fresh = [a for a in articles if a["link"]]
        print(
            f"{len(fresh)} to judge (dry run, ignoring seen-history)", file=sys.stderr
        )
    else:
        fresh = [a for a in articles if a["link"] and not is_seen(con, a["link"])]
        print(f"{len(fresh)} new since last run", file=sys.stderr)

    kept = []
    for a in fresh:
        v = classify(a["title"], a["summary"], a["source"])
        if con is not None:
            mark_seen(con, a["link"])
        passed = keep(v)
        if show_verdicts and v is not None:
            mark = "\u2713" if passed else "\u2717"
            flags = []
            if v.get("is_corporate_pr"):
                flags.append("PR")
            if v.get("is_pure_luck"):
                flags.append("luck")
            tag = f"  [{','.join(flags)}]" if flags else ""
            print(
                f"  {mark} {float(v.get('optimism', 0)):.2f} {v.get('category', '?'):17} "
                f"{a['title'][:70]}{tag}",
                file=sys.stderr,
            )
            print(f"        {v.get('reason', '')}", file=sys.stderr)
        if passed and v is not None:
            a.update(
                category=v["category"],
                optimism=float(v["optimism"]),
                reason=v["reason"],
            )
            kept.append(a)
    if con is not None:
        con.commit()
    print(f"{len(kept)} passed the filter", file=sys.stderr)

    by_cat: dict[str, list[dict]] = {}
    for a in kept:
        by_cat.setdefault(a["category"], []).append(a)

    selected = []
    for cat, group in by_cat.items():
        group = dedupe(group)
        group.sort(key=lambda x: -x["optimism"])
        selected.extend(group[:MAX_PER_CATEGORY])

    if not selected:
        print(
            "No good news cleared the bar. Try lowering OPTIMISM_THRESHOLD.",
            file=sys.stderr,
        )
        return

    selected.sort(key=lambda x: -x["optimism"])
    md = write_digest(selected)
    today = datetime.date.today().isoformat()
    document = f"# Good News \u2014 {today}\n\n{md}\n"

    if dry_run:
        # Progress goes to stderr above, so stdout is just the clean digest.
        print("\n" + "=" * 60 + "\n", file=sys.stderr)
        print(document)
        if send_mail:  # opt-in during dry runs, for testing the email path
            send_email(f"Good News — {today}", document)
        return

    out = OUT_DIR / f"briefing-{today}.md"
    out.write_text(document)
    print(f"wrote {out}", file=sys.stderr)
    if OPEN_WHEN_DONE and sys.platform == "darwin":
        subprocess.run(["open", str(out)])
    if send_mail:
        send_email(f"Good News — {today}", document)


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Good news evening briefing")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="test run: ignore the seen-history, print the digest to the terminal, and "
        "don't save or open a file (safe to run over and over)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="entries to pull per feed (default: 5 in --dry-run, else 25)",
    )
    p.add_argument(
        "--verdicts",
        action="store_true",
        help="print every article's keep/drop decision and reason; great for tuning CRITERIA",
    )
    p.add_argument(
        "--email",
        action="store_true",
        help="also send the briefing during a --dry-run (for testing the email path)",
    )
    p.add_argument(
        "--no-email",
        action="store_true",
        help="suppress the email on a real run (it otherwise sends when EMAIL_TO is set)",
    )
    args = p.parse_args()
    # Real runs email by default; dry runs only when --email is passed.
    send_mail = args.email if args.dry_run else not args.no_email
    main(
        dry_run=args.dry_run,
        limit=args.limit,
        show_verdicts=args.verdicts,
        send_mail=send_mail,
    )

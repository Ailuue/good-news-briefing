"""Delivering a finished briefing: write it to disk, open it (macOS), email it.

Email auth uses a Gmail app password from GMAIL_APP_PASSWORD; if it (or the
EMAIL_FROM/EMAIL_TO config) is missing, send_email no-ops with a warning rather
than failing the run.
"""

from __future__ import annotations
import os
import sys
import pathlib
import subprocess

from . import config


def write_briefing(document: str, today: str) -> pathlib.Path:
    """Save the briefing under OUT_DIR and (on macOS) open it. Returns the path."""
    out = config.OUT_DIR / f"briefing-{today}.md"
    out.write_text(document)
    print(f"wrote {out}", file=sys.stderr)
    if config.OPEN_WHEN_DONE and sys.platform == "darwin":
        subprocess.run(["open", str(out)])
    return out


def send_email(subject: str, markdown_text: str) -> None:
    """Mail the briefing via Gmail SMTP. No-ops (with a warning) if unconfigured."""
    import smtplib
    import ssl
    from email.message import EmailMessage

    recipients = [
        a for a in config.EMAIL_TO if a and "@" in a and "example.com" not in a
    ]
    if not config.EMAIL_FROM or not recipients:
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
    msg["From"] = config.EMAIL_FROM
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
        # SSL_CERT_FILE was pointed at certifi (see config), so the default
        # context verifies Gmail's cert cleanly here too.
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, context=ctx) as s:
            s.login(config.EMAIL_FROM, password)
            s.send_message(msg)
        print(f"  emailed briefing to {', '.join(recipients)}", file=sys.stderr)
    except Exception as e:
        print(f"  ! email failed: {e}", file=sys.stderr)

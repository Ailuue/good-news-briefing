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
from urllib.parse import quote

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

    base_html = None
    try:
        import markdown as _md

        base_html = _md.markdown(markdown_text)
    except ImportError:
        print(
            "  ! 'markdown' not installed; sending plain text only (pip install markdown)",
            file=sys.stderr,
        )

    def _unsub_mailto(recipient: str) -> str:
        """A mailto: link that emails EMAIL_FROM asking to drop this recipient."""
        body = quote(f"Please remove {recipient} from the good news digest.")
        return f"mailto:{config.EMAIL_FROM}?subject={quote('Unsubscribe')}&body={body}"

    def _build(recipient: str) -> EmailMessage:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = config.EMAIL_FROM
        msg["To"] = recipient  # one address per message — recipients never see each other
        # RFC 2369: mail clients render a native "Unsubscribe" button from this.
        msg["List-Unsubscribe"] = f"<{_unsub_mailto(recipient)}>"

        footer = (
            f"\n\n---\nYou're getting this because {recipient} is on a personal "
            f"good-news digest list. To stop, reply to this email or write to "
            f"{config.EMAIL_FROM}."
        )
        msg.set_content(markdown_text + footer)  # plain-text fallback
        if base_html is not None:
            footer_html = (
                f'<hr><p style="color:#888;font-size:12px">You\'re getting this '
                f"because {recipient} is on a personal good-news digest list. "
                f'<a href="{_unsub_mailto(recipient)}">Unsubscribe</a>.</p>'
            )
            msg.add_alternative(
                f"<html><body>{base_html}{footer_html}</body></html>", subtype="html"
            )
        return msg

    try:
        # SSL_CERT_FILE was pointed at certifi (see config), so the default
        # context verifies Gmail's cert cleanly here too.
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, context=ctx) as s:
            s.login(config.EMAIL_FROM, password)
            sent = []
            for r in recipients:
                try:
                    s.send_message(_build(r))
                    sent.append(r)
                except Exception as e:
                    print(f"  ! email to {r} failed: {e}", file=sys.stderr)
        if sent:
            print(f"  emailed briefing to {', '.join(sent)}", file=sys.stderr)
    except Exception as e:
        print(f"  ! email failed: {e}", file=sys.stderr)

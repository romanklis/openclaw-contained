#!/usr/bin/env python3
"""Seed the local GreenMail mirror with a small, realistic investor inbox.

The seeder is idempotent: a sentinel file in the mounted state volume prevents
duplicate messages on repeated `make example-up` runs. `scripts/reset.sh`
removes the volume to re-seed.
"""
from __future__ import annotations

import os
import pathlib
import smtplib
import sys
import time
from email.message import EmailMessage

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

SMTP_HOST = os.getenv("SMTP_HOST", "greenmail")
SMTP_PORT = int(os.getenv("SMTP_PORT", "3025"))
MAIL_FROM = os.getenv("MAIL_FROM", "taskforge@local.test")
MAIL_TO = os.getenv("MAIL_TO", "taskforge@local.test")
STATE = pathlib.Path(os.getenv("SEED_STATE", "/state/seeded"))


def wait_for_smtp(attempts: int = 30, delay: float = 2.0) -> None:
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=5) as smtp:
                smtp.ehlo()
            return
        except Exception as exc:  # noqa: BLE001 - startup race is expected
            last_error = exc
            print(f"  waiting for SMTP {SMTP_HOST}:{SMTP_PORT} ({attempt}/{attempts})")
            time.sleep(delay)
    raise RuntimeError(f"SMTP not reachable: {last_error}")


def pitch_deck_pdf(path: pathlib.Path) -> None:
    c = canvas.Canvas(str(path), pagesize=A4)
    y = 265 * mm
    lines = [
        ("NovaGrid AI", 24, True),
        ("Series A — $12M at $60M pre-money", 14, False),
        ("", 8, False),
        ("Sector: Industrial AI / grid orchestration software", 12, False),
        ("Website: https://novagrid.ai", 12, False),
        ("Stage: Series A", 12, False),
        ("Traction: $3.1M ARR, 14 enterprise pilots, 140% NRR", 12, False),
        ("Customers: 3 signed utilities (Northwind, Helios, Meridian)", 12, False),
        ("Thesis: AI-native orchestration for utilities and microgrids", 12, False),
        ("", 8, False),
        ("Use of funds: product, grid integrations, and GTM in North America.", 11, False),
    ]
    for text, size, bold in lines:
        if text:
            c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
            c.drawString(20 * mm, y, text)
        y -= (size + 7) * (mm / 2.84)
    c.save()


def message(
    *,
    msg_id: str,
    sender: str,
    subject: str,
    body: str,
    attachment: pathlib.Path | None = None,
) -> EmailMessage:
    msg = EmailMessage()
    msg["Message-ID"] = f"<{msg_id}@email-intelligence.local>"
    msg["From"] = sender
    msg["To"] = MAIL_TO
    msg["Subject"] = subject
    msg.set_content(body)
    if attachment and attachment.is_file():
        msg.add_attachment(
            attachment.read_bytes(),
            maintype="application",
            subtype="pdf",
            filename="novagrid-series-a-deck.pdf",
        )
    return msg


def main() -> int:
    if STATE.exists():
        print(f"  seed sentinel present at {STATE}; skipping")
        return 0

    wait_for_smtp()
    deck = pathlib.Path("/tmp/novagrid-series-a-deck.pdf")
    pitch_deck_pdf(deck)

    messages = [
        message(
            msg_id="seed-novagrid-pitch",
            sender="Maya Chen <maya@novagrid.ai>",
            subject="Series A — NovaGrid AI (deck attached)",
            body=(
                "Hi team,\n\n"
                "NovaGrid AI is raising a $12M Series A at a $60M pre-money valuation. "
                "We build AI-native grid orchestration software for utilities and "
                "industrial microgrids.\n\n"
                "Traction: $3.1M ARR, 14 enterprise pilots, 140% net revenue retention, "
                "and 3 signed utility customers. Sector: industrial AI / grid software. "
                "Website: https://novagrid.ai\n\n"
                "Deck attached. We are targeting a close in Q1 2027.\n\n"
                "Best,\nMaya Chen\nCEO, NovaGrid AI\n"
            ),
            attachment=deck,
        ),
        message(
            msg_id="seed-investor-thesis",
            sender="Daniel Ruiz <daniel@northstar-ventures.example>",
            subject="Investor thesis: industrial AI and grid software",
            body=(
                "Hi team,\n\n"
                "Sharing our current thesis for Q1. Northstar Ventures invests "
                "$2M–$8M at seed and Series A in industrial AI, grid software, and "
                "climate-resilient infrastructure. We focus on companies with a clear "
                "data moat, utility or industrial distribution, and evidence of "
                "operational ROI.\n\n"
                "We are happy to review decks that match this focus.\n\n"
                "Daniel\n"
            ),
        ),
        message(
            msg_id="seed-novagrid-followup",
            sender="Maya Chen <maya@novagrid.ai>",
            subject="Re: Intro — quick follow-up",
            body=(
                "Hi team,\n\n"
                "Following up on the NovaGrid deck I sent last week. Happy to walk "
                "through the utility pipeline and the 140% NRR cohort analysis. "
                "Would a 30-minute call next Tuesday or Wednesday work?\n\n"
                "Thanks,\nMaya\n"
            ),
        ),
        message(
            msg_id="seed-meeting-request",
            sender="Priya Shah <priya@northstar-ventures.example>",
            subject="Meeting request: portfolio review next week",
            body=(
                "Hi team,\n\n"
                "Could we schedule a portfolio review next week? We would like to "
                "cover pipeline, follow-on capacity, and the Q1 deployment plan. "
                "Tuesday 10:00 or Thursday 14:00 works on our side.\n\n"
                "Best,\nPriya\n"
            ),
        ),
        message(
            msg_id="seed-invoice",
            sender="Cloud Vendor Billing <billing@cloudvendor.example>",
            subject="Invoice INV-2026-114",
            body=(
                "Your invoice INV-2026-114 for $4,820.00 is available. "
                "Payment is due on 2026-12-01. This is an automated message.\n"
            ),
        ),
    ]

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
        smtp.ehlo()
        for msg in messages:
            smtp.send_message(msg)
            print(f"  seeded: {msg['Subject']}")

    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text("seeded\n")
    print(f"  mailbox seeded with {len(messages)} messages")
    return 0


if __name__ == "__main__":
    sys.exit(main())

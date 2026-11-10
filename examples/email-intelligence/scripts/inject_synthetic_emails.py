#!/usr/bin/env python3
"""Inject a synthetic batch of emails into the email-intelligence mirror mailbox."""
from __future__ import annotations

import argparse
import random
import smtplib
import sys
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime

COMPANIES: list[dict[str, str]] = [
    {
        "name": "NovaGrid AI",
        "founder": "Maya Chen",
        "email": "maya@novagrid.ai",
        "sector": "industrial AI and grid software",
        "stage": "Series A",
        "capital": "$12M",
        "valuation": "$60M pre-money",
        "traction": "$3.1M ARR, 14 enterprise pilots and 140% net revenue retention",
        "website": "https://novagrid.ai",
        "thesis": "AI-native orchestration for utilities and microgrids",
    },
    {
        "name": "HelioForge",
        "founder": "Lucas Meyer",
        "email": "lucas@helioforge.example",
        "sector": "solar manufacturing software",
        "stage": "Seed",
        "capital": "$4M",
        "valuation": "$18M post-money",
        "traction": "6 paid pilots and 22% month-over-month pipeline growth",
        "website": "https://helioforge.example",
        "thesis": "software-defined production for distributed solar factories",
    },
    {
        "name": "Aether Motors",
        "founder": "Sofia Alvarez",
        "email": "sofia@aethermotors.example",
        "sector": "electric mobility and fleet software",
        "stage": "Series A",
        "capital": "$15M",
        "valuation": "$75M pre-money",
        "traction": "3 fleet customers, 1,200 vehicles under management and 31% gross margin",
        "website": "https://aethermotors.example",
        "thesis": "data-driven fleet electrification for logistics operators",
    },
    {
        "name": "QuantumLeap Health",
        "founder": "Dr. Amara Okafor",
        "email": "amara@quantumleap.health",
        "sector": "clinical decision support",
        "stage": "Series A",
        "capital": "$9M",
        "valuation": "$45M pre-money",
        "traction": "4 hospital deployments and 18,000 reviewed cases",
        "website": "https://quantumleap.health",
        "thesis": "evidence-grounded triage support for high-volume care teams",
    },
    {
        "name": "TerraVolt",
        "founder": "Jonas Weber",
        "email": "jonas@terravolt.example",
        "sector": "battery storage and energy trading",
        "stage": "Seed",
        "capital": "$5M",
        "valuation": "$22M post-money",
        "traction": "42 MWh under contract and 11% forecast error reduction",
        "website": "https://terravolt.example",
        "thesis": "optimization software for behind-the-meter storage",
    },
    {
        "name": "Lumen Robotics",
        "founder": "Grace Park",
        "email": "grace@lumenrobotics.example",
        "sector": "warehouse robotics",
        "stage": "Series A",
        "capital": "$11M",
        "valuation": "$55M pre-money",
        "traction": "9 production sites and 99.2% pick accuracy",
        "website": "https://lumenrobotics.example",
        "thesis": "vision-first picking for mixed-SKU warehouses",
    },
    {
        "name": "Cobalt Systems",
        "founder": "Owen Clarke",
        "email": "owen@cobaltsystems.example",
        "sector": "supply-chain visibility",
        "stage": "Seed",
        "capital": "$3.5M",
        "valuation": "$16M post-money",
        "traction": "12 enterprise accounts and 4.6x net expansion",
        "website": "https://cobaltsystems.example",
        "thesis": "event-driven visibility for critical component supply chains",
    },
    {
        "name": "Verdant Compute",
        "founder": "Ines Duarte",
        "email": "ines@verdantcompute.example",
        "sector": "energy-efficient data infrastructure",
        "stage": "Series A",
        "capital": "$13M",
        "valuation": "$65M pre-money",
        "traction": "2.1 MW deployed and 38% lower cooling overhead",
        "website": "https://verdantcompute.example",
        "thesis": "modular, low-carbon compute for edge data workloads",
    },
]

FUNDS: list[dict[str, str]] = [
    {
        "name": "Northstar Ventures",
        "partner": "Daniel Ruiz",
        "email": "daniel@northstar-ventures.example",
        "check": "$2M-$8M",
        "focus": "industrial AI, grid software and climate-resilient infrastructure",
    },
    {
        "name": "Meridian Growth",
        "partner": "Priya Shah",
        "email": "priya@meridiangrowth.example",
        "check": "$5M-$15M",
        "focus": "applied AI, logistics software and operational workflows",
    },
    {
        "name": "Harborline Capital",
        "partner": "Elena Rossi",
        "email": "elena@harborline.example",
        "check": "$1M-$6M",
        "focus": "healthcare operations, data infrastructure and vertical SaaS",
    },
]


def _slug(value: str) -> str:
    return "-".join("".join(ch.lower() if ch.isalnum() else " " for ch in value).split())


def _pdf_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _build_pdf(lines: list[str]) -> bytes:
    content = ["BT", "/F1 12 Tf", "72 720 Td"]
    for index, line in enumerate(lines):
        if index:
            content.append("0 -18 Td")
        content.append(f"({_pdf_escape(line)}) Tj")
    content.append("ET")
    stream = "\n".join(content).encode("latin-1", "replace")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]

    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"

    xref_offset = len(output)
    output += f"xref\n0 {len(objects) + 1}\n".encode()
    output += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        output += f"{offset:010d} 00000 n \n".encode()
    output += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode()
    return bytes(output)


def _categories(count: int, rng: random.Random) -> list[str]:
    base = ["new_pitch", "investor_thesis", "follow_up", "meeting_request", "other"]
    items = [base[index % len(base)] for index in range(count)]
    rng.shuffle(items)
    return items


def _new_pitch(
    rng: random.Random,
    company: dict[str, str],
    to: str,
    message_id: str,
    batch: str,
    attach: bool,
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = f"{company['founder']} <{company['email']}>"
    msg["To"] = to
    msg["Subject"] = (
        f"{company['stage']} - {company['name']} (deck attached)"
        if attach
        else f"New pitch - {company['name']}"
    )
    msg["Message-ID"] = message_id
    msg["Date"] = format_datetime(datetime.now(timezone.utc))
    msg["X-Synthetic-Batch"] = batch
    msg.set_content(
        f"""Hi team,

{company['name']} is raising {company['capital']} at {company['valuation']}.
We build {company['sector']}; our focus is {company['thesis']}.

Traction: {company['traction']}.
Website: {company['website']}

{'The deck is attached.' if attach else 'Happy to share the deck.'}
We are targeting a close next quarter.

Best,
{company['founder']}
CEO, {company['name']}
"""
    )
    if attach:
        msg.add_attachment(
            _build_pdf(
                [
                    f"{company['name']} - {company['stage']}",
                    f"Raising: {company['capital']}",
                    f"Valuation: {company['valuation']}",
                    f"Sector: {company['sector']}",
                    f"Traction: {company['traction']}",
                    f"Website: {company['website']}",
                    f"Thesis: {company['thesis']}",
                ]
            ),
            maintype="application",
            subtype="pdf",
            filename=f"{_slug(company['name'])}-deck.pdf",
        )
    return msg


def _investor_thesis(
    rng: random.Random,
    fund: dict[str, str],
    to: str,
    message_id: str,
    batch: str,
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = f"{fund['partner']} <{fund['email']}>"
    msg["To"] = to
    msg["Subject"] = f"Investor thesis: {fund['focus'].split(',')[0]}"
    msg["Message-ID"] = message_id
    msg["Date"] = format_datetime(datetime.now(timezone.utc))
    msg["X-Synthetic-Batch"] = batch
    msg.set_content(
        f"""Hi team,

Sharing our current thesis. {fund['name']} invests {fund['check']} at seed
and Series A in {fund['focus']}.

We look for a clear data moat, credible distribution and evidence of
operational ROI. We are happy to review decks that match this focus.

Best,
{fund['partner']}
"""
    )
    return msg


def _follow_up(
    rng: random.Random,
    company: dict[str, str],
    to: str,
    message_id: str,
    batch: str,
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = f"{company['founder']} <{company['email']}>"
    msg["To"] = to
    msg["Subject"] = f"Re: Intro - {company['name']} follow-up"
    msg["Message-ID"] = message_id
    msg["Date"] = format_datetime(datetime.now(timezone.utc))
    msg["X-Synthetic-Batch"] = batch
    msg.set_content(
        f"""Hi team,

Following up on the {company['name']} deck I sent last week. Happy to walk
through the pipeline and the cohort analysis. Would a 30-minute call next
Tuesday or Wednesday work?

Thanks,
{company['founder']}
"""
    )
    return msg


def _meeting_request(
    rng: random.Random,
    to: str,
    message_id: str,
    batch: str,
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = "Priya Shah <priya@northstar-ventures.example>"
    msg["To"] = to
    msg["Subject"] = "Meeting request: portfolio review next week"
    msg["Message-ID"] = message_id
    msg["Date"] = format_datetime(datetime.now(timezone.utc))
    msg["X-Synthetic-Batch"] = batch
    msg.set_content(
        """Hi team,

Could we schedule a portfolio review next week? We would like to cover
pipeline, follow-on capacity and the next deployment plan. Tuesday 10:00
or Thursday 14:00 works on our side.

Best,
Priya
"""
    )
    return msg


def _other(
    rng: random.Random,
    index: int,
    to: str,
    message_id: str,
    batch: str,
) -> EmailMessage:
    invoice = f"INV-2026-{100 + index:03d}"
    msg = EmailMessage()
    msg["From"] = "Cloud Vendor Billing <billing@cloudvendor.example>"
    msg["To"] = to
    msg["Subject"] = f"Invoice {invoice}"
    msg["Message-ID"] = message_id
    msg["Date"] = format_datetime(datetime.now(timezone.utc))
    msg["X-Synthetic-Batch"] = batch
    msg.set_content(
        f"""Your invoice {invoice} for $4,820.00 is available.
Payment is due on 2026-12-01. This is an automated message.
"""
    )
    return msg


def _build_messages(
    count: int,
    rng: random.Random,
    run_id: str,
    to: str,
    attachments: bool,
) -> list[tuple[str, EmailMessage]]:
    categories = _categories(count, rng)
    companies = COMPANIES[:]
    rng.shuffle(companies)
    messages: list[tuple[str, EmailMessage]] = []

    for index, category in enumerate(categories):
        message_id = f"<synthetic-{run_id}-{index:02d}@email-intelligence.local>"
        company = companies[index % len(companies)]
        fund = rng.choice(FUNDS)
        if category == "new_pitch":
            msg = _new_pitch(rng, company, to, message_id, run_id, attachments)
        elif category == "investor_thesis":
            msg = _investor_thesis(rng, fund, to, message_id, run_id)
        elif category == "follow_up":
            msg = _follow_up(rng, company, to, message_id, run_id)
        elif category == "meeting_request":
            msg = _meeting_request(rng, to, message_id, run_id)
        else:
            msg = _other(rng, index, to, message_id, run_id)
        messages.append((category, msg))
    return messages


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inject a synthetic batch of emails into the GreenMail mirror."
    )
    parser.add_argument("--count", type=int, default=None, help="Number of emails (default: random 5-10).")
    parser.add_argument("--seed", type=int, default=None, help="Deterministic content and Message-IDs.")
    parser.add_argument("--smtp-host", default="127.0.0.1")
    parser.add_argument("--smtp-port", type=int, default=3025)
    parser.add_argument("--to", default="taskforge@local.test")
    parser.add_argument("--username", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--starttls", action="store_true")
    parser.add_argument("--no-attachments", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    rng = random.Random(args.seed)
    count = args.count if args.count is not None else rng.randint(5, 10)
    if count < 1:
        print("count must be at least 1", file=sys.stderr)
        return 2

    run_id = (
        f"seed{args.seed}"
        if args.seed is not None
        else f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
    )
    batch = _build_messages(
        count=count,
        rng=rng,
        run_id=run_id,
        to=args.to,
        attachments=not args.no_attachments,
    )

    label = "email" if len(batch) == 1 else "emails"
    print(f"Batch: {len(batch)} synthetic {label} (run={run_id})")
    for category, msg in batch:
        print(f"  [{category}] {msg['Subject']}  {msg['Message-ID']}")

    if args.dry_run:
        print("Dry run: nothing sent.")
        return 0

    try:
        with smtplib.SMTP(args.smtp_host, args.smtp_port, timeout=args.timeout) as smtp:
            smtp.ehlo()
            if args.starttls:
                smtp.starttls()
                smtp.ehlo()
            if args.username:
                smtp.login(args.username, args.password)
            for _, msg in batch:
                smtp.send_message(msg)
    except (OSError, smtplib.SMTPException) as exc:
        print(f"Could not inject mail: {exc}", file=sys.stderr)
        print(
            "Is the example running? Start it with: "
            "make example-up NAME=email-intelligence",
            file=sys.stderr,
        )
        return 1

    sent_label = "message" if len(batch) == 1 else "messages"
    print(
        f"Injected {len(batch)} {sent_label} into {args.to} "
        f"via {args.smtp_host}:{args.smtp_port}."
    )
    print("Next: Dashboard -> Settings -> Run now, or wait for the scheduler interval.")
    if count > 50:
        print("Note: the gateway scans at most 50 INBOX messages per run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
